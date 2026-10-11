"""Security regression tests: every hardening in the code is pinned by an attack that used to work or could."""
import asyncio
import copy
import json
import re
import subprocess
import time
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from storymap_gen import publish
from storymap_gen.claims import AreaClaim, OrientationClaim, StatClaim, validate_regex
from storymap_gen.safe_expr import safe_filter
from storymap_gen.spec import StorySpec
from storymap_gen.verify import verify_claims
from tests.conftest import FIXTURES, ROOT

# ---- 1. a model-written `where` must never be evaluated as code -------------------------------------
FRAME = pd.DataFrame({"name": ["a", None, "b"], "type": ["park", "park", "plaza"], "w": [10, 40, 60]})


@pytest.mark.parametrize("expr, rows", [
    ("name.notna()", [0, 2]),
    ("type == 'park'", [0, 1]),
    ("w >= 30 and w < 50", [1]),
    ("type in ['plaza', 'x']", [2]),
    ("not name.isna()", [0, 2]),
    ("(w == 10) or (type != 'park')", [0, 2]),
])
def test_safe_filter_supports_the_documented_language(expr, rows):
    assert list(safe_filter(FRAME, expr).index) == rows


@pytest.mark.parametrize("evil", [
    "__import__('os').system('echo pwned')", "name.str.contains('a')", "w + 1 > 3", "(1).__class__", "open('x')",
    "name.notna() == __builtins__", "w > df", "x == 1", "[i for i in w]", "lambda: 1", "w if w else 0",
    "name.apply(print)", "name.notna(1)", "w[0] == 1", "f'{w}' == 'a'", "x" * 400,
])
def test_safe_filter_rejects_everything_else(evil):
    with pytest.raises(ValueError):
        safe_filter(FRAME, evil)


def test_hostile_where_reaches_the_user_as_an_error_not_as_code_execution(jh_store, tmp_path, monkeypatch):
    marker = tmp_path / "pwned.txt"
    payload = f"__import__('pathlib').Path(r'{marker}').write_text('x')"
    spec = {"title": "t", "template": "longform", "sections": [{"title": "s"}],
            "claims": [{"kind": "layer_stat", "id": "a", "text": "t", "layer": "streets_centerline", "agg": "count",
                        "expected": 1, "where": payload},
                       {"kind": "layer_area", "id": "b", "text": "t", "layer": "parks_", "expected": 1, "where": payload}]}
    results = verify_claims(StorySpec.model_validate(spec), jh_store)
    assert [r.status for r in results] == ["ERROR", "ERROR"] and not marker.exists()


# ---- 2. a model-written regex must not be able to hang the process ------------------------------------
@pytest.mark.parametrize("pattern", ["(a+)+$", "(a|aa)*b", "(a*)*", "(?=x)y", r"(a)\1", r"(?P<x>a)(?P=x)", "a" * 200, "[", "(a+){2,}"])
def test_dangerous_regexes_are_refused_quickly(pattern):
    t0 = time.perf_counter()
    with pytest.raises(ValueError):
        validate_regex(pattern)
    assert time.perf_counter() - t0 < 0.5


def test_default_grid_regex_is_accepted_and_redos_input_cannot_reach_matching():
    assert validate_regex(r"^\d+ (AVE|ST|RD|PL|DR|LN)$")
    with pytest.raises(ValidationError):
        OrientationClaim(id="o", text="t", street="X", relation="parallel_to_grid", grid_regex="(a+)+$")


# ---- 3. the token may only go to ArcGIS, and never be re-sent after a redirect ------------------------------
@pytest.mark.parametrize("url", [
    "http://www.arcgis.com/sharing/rest", "https://evil.com/sharing/rest", "https://www.arcgis.com.evil.com/sharing/rest",
    "https://evilarcgis.com/sharing/rest", "https://www.arcgis.com@evil.com/sharing/rest",
    "https://user:pw@www.arcgis.com/sharing/rest", "https://www.arcgis.com:8443/sharing/rest",
    "https://www.arcgis.com/sharing/rest?x=1", "https://www.arcgis.com/other", "https://localhost/sharing/rest",
    "https://169.254.169.254/sharing/rest", "ftp://www.arcgis.com/sharing/rest", "",
])
def test_hostile_portal_urls_are_refused(url):
    with pytest.raises(ValueError):
        publish.validate_portal(url)


def test_legitimate_portals_and_enterprise_allowlist(monkeypatch):
    assert publish.validate_portal("https://www.arcgis.com/sharing/rest") == "https://www.arcgis.com/sharing/rest"
    assert publish.validate_portal("https://myorg.maps.arcgis.com/sharing/rest/") == "https://myorg.maps.arcgis.com/sharing/rest"
    with pytest.raises(ValueError):
        publish.validate_portal("https://gis.example.edu/portal/sharing/rest")
    monkeypatch.setenv("STORYMAP_ALLOWED_PORTAL_HOSTS", "gis.example.edu")
    assert publish.validate_portal("https://gis.example.edu/portal/sharing/rest").startswith("https://gis.example.edu")


@pytest.mark.parametrize("name", ["../x", "a/b", "a?b", "a#b", "x" * 200, "", "a b", "a\nb"])
def test_user_names_cannot_alter_the_request_path(name):
    with pytest.raises(ValueError):
        publish.validate_username(name)


def test_no_request_is_made_to_a_hostile_portal(monkeypatch):
    monkeypatch.setattr(publish.requests, "post", lambda *a, **k: pytest.fail("a request was sent to a refused portal"))
    with pytest.raises(ValueError):
        publish.publish_draft({"root": "r", "nodes": {}, "resources": {}}, "t", "user", "TOKEN123", sharing="https://evil.com/sharing/rest")


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def test_redirects_are_never_followed_and_the_token_never_leaks_into_errors(monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return _Resp(307, {})

    monkeypatch.setattr(publish.requests, "post", fake_post)
    with pytest.raises(RuntimeError) as e:
        publish.publish_draft({"root": "r", "nodes": {}, "resources": {}}, "t", "user", "SECRET-TOKEN-VALUE")
    assert seen["allow_redirects"] is False and seen["timeout"] == publish.TIMEOUT
    assert "SECRET-TOKEN-VALUE" not in str(e.value)


@pytest.mark.parametrize("resp", [_Resp(200, None), _Resp(200, {"error": {"code": 498, "message": "Invalid token SECRET-TOKEN-VALUE"}})])
def test_portal_errors_are_reported_without_a_traceback_dump(monkeypatch, resp):
    monkeypatch.setattr(publish.requests, "post", lambda *a, **k: resp)
    with pytest.raises(RuntimeError):
        publish.publish_draft({"root": "r", "nodes": {}, "resources": {}}, "t", "user", "SECRET-TOKEN-VALUE")


def test_unexpected_item_id_from_the_portal_is_rejected(monkeypatch):
    monkeypatch.setattr(publish.requests, "post", lambda *a, **k: _Resp(200, {"id": "../../etc/passwd"}))
    with pytest.raises(RuntimeError, match="unexpected item id"):
        publish.publish_draft({"root": "r", "nodes": {}, "resources": {}}, "t", "user", "TOKEN123")


def test_cli_publish_refuses_a_hostile_portal_before_any_network(tmp_path, monkeypatch):
    from storymap_gen.__main__ import main
    monkeypatch.setenv("ARCGIS_TOKEN", "TOKEN123")
    monkeypatch.setattr(publish.requests, "post", lambda *a, **k: pytest.fail("network used"))
    story = tmp_path / "s.json"
    story.write_text(json.dumps({"root": "r", "nodes": {"r": {"type": "story"}}, "resources": {}}), encoding="utf-8")
    assert main(["publish", str(story), "--title", "t", "--username", "u", "--portal", "https://evil.com/sharing/rest"]) == 2


# ---- 4. inputs are bounded ---------------------------------------------------------------------------------
def base_spec():
    return {"title": "t", "template": "longform", "sections": [{"title": "s"}]}


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(sections=[{"title": "s"}] * 51),
    lambda d: d["sections"][0].update(slides=[{"title": "x", "body": "y"}] * 51),
    lambda d: d["sections"][0].update(slides=[{"title": "x", "body": "y" * 5001}]),
    lambda d: d["sections"][0].update(intro="y" * 10_001),
    lambda d: d.update(sections=[{"title": "s", "slides": [{"title": "x", "body": "y" * 5000}] * 50}] * 3),  # > total budget
    lambda d: d.update(title="t" * 301),
    lambda d: d.update(claims=[{"kind": "layer_stat", "id": f"c{i}", "text": "t", "layer": "l", "agg": "count", "expected": 1}
                               for i in range(501)]),
    lambda d: d["sections"][0].update(image_url="javascript:alert(1)"),
    lambda d: d["sections"][0].update(image_url="http://insecure.example/x.png"),
    lambda d: d["sections"][0].update(image_url="file:///etc/passwd"),
    lambda d: d.update(cover_image_url="data:text/html,<script>1</script>"),
    lambda d: d.update(theme="../../x"),
    lambda d: d.update(locale="en_US; drop"),
    lambda d: d["sections"][0].update(slides=[{"title": "x", "body": "y", "map": {"item_id": "../../etc/passwd"}}]),
    lambda d: d["sections"][0].update(slides=[{"title": "x", "body": "y", "map": {"item_id": "a" * 32, "extent": [0, 0, float("nan"), 1]}}]),
    lambda d: d["sections"][0].update(slides=[{"title": "x", "body": "y", "map": {}}]),
    lambda d: d.update(allow_numbers=[float("inf")]),
    lambda d: d.update(sources=[{"key": "bad key!", "text": "t"}]),
])
def test_oversized_or_malformed_specs_are_rejected(mutate):
    d = copy.deepcopy(base_spec())
    mutate(d)
    with pytest.raises(ValidationError):
        StorySpec.model_validate(d)


def test_claim_numbers_must_be_finite_and_ids_well_formed():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            StatClaim(id="a", text="t", layer="l", agg="count", expected=bad)
        with pytest.raises(ValidationError):
            AreaClaim(id="a", text="t", layer="l", expected=1, tolerance=bad)
    with pytest.raises(ValidationError):
        StatClaim(id="has space", text="t", layer="l", agg="count", expected=1)
    with pytest.raises(ValidationError):
        StatClaim(id="a", text="t", layer="l", agg="count", expected=1, tolerance=-1)


# ---- 5. MCP: untrusted arguments ----------------------------------------------------------------------------
def _mcp(coro):
    from mcp import Client

    from storymap_gen.mcp_server import server

    async def go():
        async with Client(server) as client:
            return await coro(client)

    return asyncio.run(go())


def _payload(result):
    return json.loads(result.content[0].text)


def test_mcp_reads_only_inside_the_working_directory_by_default(tmp_path, monkeypatch):
    outside = tmp_path / "secret.geojson"
    outside.write_text('{"type":"FeatureCollection","features":[]}')
    monkeypatch.delenv("STORYMAP_DATA_ROOT", raising=False)
    monkeypatch.chdir(ROOT)

    async def go(c):
        return _payload(await c.call_tool("verify_spec", {"spec": base_spec(), "data": {"x": str(outside)}}))

    r = _mcp(go)
    assert r["ok"] is False and r["error"] == "bad_input" and str(tmp_path) not in json.dumps(r)


def test_mcp_never_reads_non_gis_files(monkeypatch):
    monkeypatch.chdir(ROOT)

    async def go(c):
        return [_payload(await c.call_tool("verify_spec", {"spec": base_spec(), "data": {"x": p}}))
                for p in ("pyproject.toml", "README.md", ".gitignore", "LICENSE")]

    assert all(r["ok"] is False and r["error"] == "bad_input" for r in _mcp(go))


def test_mcp_tools_never_raise_and_hide_internal_details(monkeypatch):
    import storymap_gen.mcp_server as m
    monkeypatch.setattr(m, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom at C:\\Users\\someone\\secret")))
    monkeypatch.chdir(ROOT)

    async def go(c):
        bad = _payload(await c.call_tool("verify_spec", {"spec": base_spec(), "data": {}}))
        again = _payload(await c.call_tool("get_spec_schema", {}))   # the server is still alive
        return bad, again

    bad, again = _mcp(go)
    assert bad == {"ok": False, "error": "internal_error", "message": "RuntimeError"} and again["ok"] is True


def test_mcp_rejects_oversized_specs_and_deeply_nested_garbage(monkeypatch):
    monkeypatch.chdir(ROOT)
    huge = {"title": "t", "sections": [{"title": "s", "intro": "x" * 10_000}] * 50, "pad": "y" * 2_100_000}
    nested: object = "x"
    for _ in range(40):
        nested = [nested]

    async def go(c):
        return (_payload(await c.call_tool("verify_spec", {"spec": huge, "data": {}})),
                _payload(await c.call_tool("verify_spec", {"spec": {"title": nested}, "data": {}})))

    a, b = _mcp(go)
    assert a["ok"] is False and b["ok"] is False


# ---- 6. repository hygiene: no secrets, private paths or real e-mail addresses in tracked files ------------------
SECRET_PATTERNS = {
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    "GitHub fine-grained token": r"github_pat_[A-Za-z0-9_]{30,}",
    "AWS key": r"\bAKIA[0-9A-Z]{16}\b",
    "API key": r"\bsk-[A-Za-z0-9_-]{30,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "OAuth token in a URL": r"access_token=[A-Za-z0-9_.-]{20,}",
    "ArcGIS token": r"\b[A-Za-z0-9_-]{20,}\.\.[A-Za-z0-9_-]{40,}",
    "Windows user path": r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+",
    "Unix home path": r"/(?:home|Users)/[a-z][a-z0-9_.-]{2,}/",
}
ALLOWED_EMAIL = re.compile(r"(noreply|example\.(com|org|edu)|@users\.noreply\.github\.com)")


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [ROOT / f for f in out.stdout.splitlines() if (ROOT / f).is_file()]


def test_no_secrets_private_paths_or_personal_emails_in_tracked_files():
    problems = []
    for path in _tracked_files():
        if path.suffix.lower() in {".png", ".jpg", ".gif", ".ico", ".whl"} or path.stat().st_size > 5_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            if re.search(pattern, text):
                problems.append(f"{path.relative_to(ROOT)}: {label}")
        # the lookbehind skips URL authorities such as https://user:pw@host (a credential in a URL, not an address)
        for email in re.findall(r"(?<![:/\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+", text):
            if not ALLOWED_EMAIL.search(email):
                problems.append(f"{path.relative_to(ROOT)}: e-mail address {email[:3]}***")
    assert problems == []


# identifiers that must never appear in the repository, kept as hashes so the test does not contain them itself
# (a NetID, an organisation sub-domain, an OAuth client id, two personal first names)
PRIVATE_WORD_HASHES = {
    "50fad00d60eb99dd9b673f99664d194f1855944d0ba0f9febc5a1a54ba98dded",
    "12a175edf660de93abff73d084ce8538a051fd3135c0da225f5fc60c298dc4f6",
    "af7a1a9be0a142334b30ec2a8cbe2cf20d136e1bdfd62b75fadfed18f8b10c75",
    "504aa7157b58de1cbcf0494b0cec16cded51336f658c0026956cb2f11fef49c7",
    "c1495dc48f3de433ee672638ce9d8c1c3f1ec9593c4a99411cbdc6b96c59bd49",
}


def test_no_private_identifiers_in_tracked_files_or_their_names():
    import hashlib
    hits = []
    for path in _tracked_files():
        if path.suffix.lower() in {".png", ".jpg", ".gif", ".ico", ".whl"} or path.stat().st_size > 8_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore") + " " + str(path.relative_to(ROOT))
        for word in set(re.findall(r"[A-Za-z0-9]{4,}", text)):
            if hashlib.sha256(word.lower().encode()).hexdigest() in PRIVATE_WORD_HASHES:
                hits.append(f"{path.relative_to(ROOT)}: a private identifier")
    assert hits == []


def test_fixtures_directory_is_part_of_the_scan():
    assert any(p.parent == FIXTURES for p in _tracked_files())


def test_ambiguous_regex_constructs_are_refused_not_warned_about():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")           # a strict environment must not turn a warning into a crash
        for pattern in ("[[a]", "[a&&b]", "[a--b]"):
            with pytest.raises(ValueError):
                validate_regex(pattern)
