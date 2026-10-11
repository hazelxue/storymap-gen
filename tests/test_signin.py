"""Signing in with the browser: a one-shot local listener, PKCE, refresh, revoke. Nothing is copied, nothing secret is echoed."""
import http.client
import json
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from storymap_gen import auth, loopback
from storymap_gen.__main__ import main

CLIENT = "abcdEFGH12345678"
PORTAL = "https://myorg.maps.arcgis.com/sharing/rest"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, path, host=None, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"localhost:{port}")
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    headers = {k.lower(): v for k, v in resp.getheaders()}
    conn.close()
    return resp.status, body, headers


def listen(state, port, timeout=5):
    """Run the listener in a thread; returns a dict that fills with 'code' or 'error' and a ready event."""
    out, ready = {}, threading.Event()

    def run():
        try:
            out["code"] = loopback.wait_for_code(state, port, timeout, on_ready=ready.set)
        except loopback.LoginError as e:
            out["error"] = str(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)
    return out, t


# ---- PKCE ------------------------------------------------------------------------------------------------------
def test_the_pkce_challenge_matches_the_rfc_example_and_every_login_gets_a_new_pair():
    assert loopback.challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    (v1, c1), (v2, c2) = loopback.pkce_pair(), loopback.pkce_pair()
    assert 43 <= len(v1) <= 128 and c1 == loopback.challenge_for(v1) and v1 != v2 and c1 != c2
    assert loopback.new_state() != loopback.new_state() and len(loopback.new_state()) >= 40


def test_the_sign_in_address_asks_for_a_code_with_pkce_and_only_goes_to_arcgis():
    url = auth.authorize_url_pkce(CLIENT, PORTAL, loopback.redirect_uri(8765), "CHAL", "STATE")
    q = parse_qs(urlsplit(url).query)
    assert urlsplit(url).netloc == "myorg.maps.arcgis.com" and urlsplit(url).path.endswith("/oauth2/authorize")
    assert q["response_type"] == ["code"] and q["code_challenge_method"] == ["S256"] and q["code_challenge"] == ["CHAL"]
    assert q["redirect_uri"] == ["http://localhost:8765/storymap-gen-callback"] and q["state"] == ["STATE"] and "access_token" not in url
    with pytest.raises(ValueError):
        auth.authorize_url_pkce(CLIENT, "https://evil.example.com/sharing/rest", loopback.redirect_uri(), "C", "S")
    with pytest.raises(ValueError, match="client id"):
        auth.authorize_url_pkce("bad id", PORTAL, loopback.redirect_uri(), "C", "S")


# ---- the listener --------------------------------------------------------------------------------------------------
def test_the_listener_takes_the_one_good_callback_and_ignores_everything_else():
    port, state = free_port(), "the-state-value"
    out, t = listen(state, port)
    assert get(port, "/storymap-gen-callback?code=C1&state=wrong")[0] == 400                        # a request from somewhere else: ignored
    assert get(port, "/storymap-gen-callback?code=C1")[0] == 400                                     # no state at all
    assert get(port, "/elsewhere?code=C1&state=" + state)[0] == 404                                  # another path
    assert get(port, "/storymap-gen-callback?code=C1&state=" + state, host="evil.example.com")[0] == 400   # another name for this computer
    for method in ("POST", "PUT", "DELETE"):
        assert get(port, "/storymap-gen-callback?code=C1&state=" + state, method=method)[0] == 405     # only a GET can complete it
    assert out == {}                                                                                  # still waiting after all of that
    status, body, _ = get(port, "/storymap-gen-callback?code=GOOD&state=" + state)
    t.join(5)
    assert status == 200 and "Signed in" in body and out == {"code": "GOOD"}


def test_the_page_it_serves_never_echoes_what_it_received_and_sets_safe_headers():
    port, state = free_port(), "s" * 43
    out, t = listen(state, port)
    status, body, headers = get(port, "/storymap-gen-callback?code=SECRETCODE123&state=" + state)
    t.join(5)
    assert status == 200 and "SECRETCODE123" not in body and state not in body and out["code"] == "SECRETCODE123"
    assert headers["cache-control"] == "no-store" and headers["referrer-policy"] == "no-referrer" and "default-src 'none'" in headers["content-security-policy"]
    port2, state2 = free_port(), "t" * 43
    out2, t2 = listen(state2, port2)
    status, body, _ = get(port2, "/storymap-gen-callback?error=%3Cscript%3Ealert(1)%3C/script%3E&state=" + state2)
    t2.join(5)
    assert status == 200 and "<script>" not in body and "alert" not in body and "unknown_error" in out2["error"]
    assert "<script>" not in out2["error"]


def test_a_refused_sign_in_is_an_error_not_a_hang():
    port, state = free_port(), "u" * 43
    out, t = listen(state, port)
    get(port, "/storymap-gen-callback?error=access_denied&state=" + state)
    t.join(5)
    assert "access_denied" in out["error"]


def test_the_listener_gives_up_after_the_timeout_and_frees_the_port():
    port = free_port()
    start = time.monotonic()
    with pytest.raises(loopback.LoginError, match="timed out"):
        loopback.wait_for_code("x", port, timeout=0.6)
    assert time.monotonic() - start < 5
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))                       # the port is free again


def test_a_busy_port_is_explained():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        with pytest.raises(loopback.LoginError, match="not available"):
            loopback.wait_for_code("x", s.getsockname()[1], timeout=0.5)


def test_it_only_listens_on_this_computer():
    import inspect
    source = inspect.getsource(loopback.wait_for_code)
    assert '("127.0.0.1", port)' in source and "0.0.0.0" not in source


# ---- the whole sign-in ------------------------------------------------------------------------------------------------
class FakePortal:
    """Answers the token, verification and revoke calls and remembers what it was sent."""

    def __init__(self, refresh=True):
        self.calls, self.refresh = [], refresh

    def __call__(self, url, **kw):
        data = kw.get("data", {})
        self.calls.append((url.rsplit("/", 1)[-1], dict(data)))
        if url.endswith("/oauth2/token"):
            if data["grant_type"] == "authorization_code":
                out = {"access_token": "ACCESS-1" + "x" * 20, "expires_in": 1800, "username": "someone"}
                if self.refresh:
                    out.update({"refresh_token": "REFRESH-1" + "y" * 20, "refresh_token_expires_in": 1209600})
                return out
            return {"access_token": "ACCESS-2" + "x" * 20, "expires_in": 1800}
        if url.endswith("/community/self"):
            return {"username": "someone", "fullName": "A Person"}
        return {"success": True}


def browser_that_approves(portal, errors=None):
    """A stand-in for the browser: reads the sign-in address, then returns to the listener with a code, like ArcGIS would."""
    def open_url(url):
        q = parse_qs(urlsplit(url).query)
        portal.sent = q
        def go():
            time.sleep(0.2)
            port = urlsplit(q["redirect_uri"][0]).port
            get(port, f"/storymap-gen-callback?code=ONE-TIME-CODE&state={q['state'][0]}")
        threading.Thread(target=go, daemon=True).start()
    return open_url


def test_login_with_the_browser_exchanges_the_code_with_the_matching_verifier_and_saves_it(monkeypatch):
    portal = FakePortal(refresh=True)
    monkeypatch.setattr(auth, "_post", portal)
    port = free_port()
    who = auth.login_with_browser(CLIENT, PORTAL, port, stay_signed_in=False, open_url=browser_that_approves(portal), timeout=10)
    assert who["username"] == "someone" and who["stays_signed_in"] is False
    name, sent = next(c for c in portal.calls if c[0] == "token")
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "ONE-TIME-CODE" and sent["redirect_uri"].endswith("/storymap-gen-callback")
    assert loopback.challenge_for(sent["code_verifier"]) == portal.sent["code_challenge"][0]            # the verifier belongs to this sign-in
    saved = json.loads(auth.token_file().read_text(encoding="utf-8"))
    assert saved["token"].startswith("ACCESS-1") and "refresh_token" not in saved                        # not kept unless asked for


def test_staying_signed_in_keeps_a_refresh_token_and_status_says_so(monkeypatch):
    portal = FakePortal(refresh=True)
    monkeypatch.setattr(auth, "_post", portal)
    who = auth.login_with_browser(CLIENT, PORTAL, free_port(), stay_signed_in=True, open_url=browser_that_approves(portal), timeout=10)
    assert who["stays_signed_in"] is True
    saved = json.loads(auth.token_file().read_text(encoding="utf-8"))
    assert saved["refresh_token"].startswith("REFRESH-1") and saved["client_id"] == CLIENT
    st = auth.status(PORTAL)
    assert st["signed_in"] and st["renews"] and 12 <= st["days_left"] <= 14 and "REFRESH" not in json.dumps(st) and "ACCESS" not in json.dumps(st)


def test_an_expired_access_token_is_renewed_silently_when_there_is_a_refresh_token(monkeypatch):
    portal = FakePortal(refresh=True)
    monkeypatch.setattr(auth, "_post", portal)
    auth.login_with_browser(CLIENT, PORTAL, free_port(), stay_signed_in=True, open_url=browser_that_approves(portal), timeout=10)
    raw = json.loads(auth.token_file().read_text(encoding="utf-8"))
    raw["expires_at"] = time.time() - 10                                         # the access token has expired
    auth.token_file().write_text(json.dumps(raw), encoding="utf-8")
    assert auth.load_token(PORTAL) is None
    token, user = auth.resolve_credentials(PORTAL)
    assert token.startswith("ACCESS-2") and user == "someone"
    again = json.loads(auth.token_file().read_text(encoding="utf-8"))
    assert again["refresh_token"] == raw["refresh_token"] and again["token"] == token            # kept, and saved


def test_a_login_that_cannot_renew_asks_you_to_sign_in_again(monkeypatch):
    monkeypatch.setattr(auth, "_post", FakePortal(refresh=True))
    auth.save_token("ACCESS" + "x" * 20, "someone", PORTAL, 1, "REFRESH" + "y" * 20, 3600, CLIENT)
    raw = json.loads(auth.token_file().read_text(encoding="utf-8"))
    raw["expires_at"] = time.time() - 10
    raw["refresh_expires_at"] = time.time() - 10                                 # both have expired
    auth.token_file().write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="not signed in"):
        auth.resolve_credentials(PORTAL)

    def failing(url, **kw):
        raise RuntimeError("ArcGIS REST error 400: invalid refresh token")
    raw["refresh_expires_at"] = time.time() + 99999
    auth.token_file().write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(auth, "_post", failing)
    assert auth.refreshed(PORTAL) is None


def test_logout_revokes_the_refresh_token_and_still_deletes_when_revoking_fails(monkeypatch):
    portal = FakePortal(refresh=True)
    monkeypatch.setattr(auth, "_post", portal)
    auth.login_with_browser(CLIENT, PORTAL, free_port(), stay_signed_in=True, open_url=browser_that_approves(portal), timeout=10)
    assert auth.delete_token() is True and not auth.token_file().exists()
    revoke = next(c for c in portal.calls if c[0] == "revokeToken")
    assert revoke[1]["auth_token"].startswith("REFRESH-1") and revoke[1]["client_id"] == CLIENT

    auth.save_token("ACCESS" + "x" * 20, "someone", PORTAL, 100, "REFRESH" + "y" * 20, 3600, CLIENT)

    def offline(url, **kw):
        raise RuntimeError("no network")
    monkeypatch.setattr(auth, "_post", offline)
    assert auth.delete_token() is True and not auth.token_file().exists()


def test_the_token_file_is_only_readable_by_its_owner_where_the_system_supports_it():
    import os
    import stat
    auth.save_token("ACCESS" + "x" * 20, "someone", PORTAL, 100)
    if os.name != "nt":
        assert stat.S_IMODE(auth.token_file().stat().st_mode) == 0o600


# ---- the command ----------------------------------------------------------------------------------------------------------
def test_the_login_command_needs_no_pasting_and_prints_no_secret(monkeypatch, capsys):
    import storymap_gen.__main__ as cli
    portal = FakePortal(refresh=True)
    monkeypatch.setattr(auth, "_post", portal)
    opener = browser_that_approves(portal)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opener(url) or True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: pytest.fail("nothing should be pasted"))
    assert main(["login", "--client-id", CLIENT, "--portal", "myorg.maps.arcgis.com", "--port", str(free_port()), "--stay-signed-in"]) == 0
    printed = capsys.readouterr()
    assert "Signed in as someone" in printed.out and "renews itself" in printed.out
    for secret in ("ACCESS-1", "REFRESH-1", "ONE-TIME-CODE", portal.sent["code_challenge"][0], portal.sent["state"][0]):
        assert secret not in printed.out + printed.err
    assert auth.load_profile()["client_id"] == CLIENT
    assert main(["status"]) == 0 and "renews itself" in capsys.readouterr().out


def test_when_the_browser_cannot_be_opened_the_address_is_printed(monkeypatch, capsys):
    import storymap_gen.__main__ as cli
    seen = {}

    def fake_login(client_id, portal, port, stay, open_url, **kw):
        open_url("https://myorg.maps.arcgis.com/sharing/rest/oauth2/authorize?client_id=x")      # the command's own opener
        seen["done"] = True
        return {"username": "someone", "stays_signed_in": False}

    monkeypatch.setattr(cli.webbrowser, "open", lambda url: False)                               # no browser on this computer
    monkeypatch.setattr(auth, "login_with_browser", fake_login)
    assert main(["login", "--client-id", CLIENT, "--no-save"]) == 0
    out = capsys.readouterr().out
    assert seen["done"] and "Open this address in your browser" in out and "oauth2/authorize" in out and "Token not saved" in out


def test_the_help_and_the_first_time_message_name_the_redirect_url(capsys):
    assert main(["login"]) == 2
    err = capsys.readouterr().err
    assert "http://localhost:8765/storymap-gen-callback" in err and "--client-id" in err


def test_a_sign_in_that_did_not_complete_is_explained_not_reported_as_an_unexpected_error(monkeypatch, capsys):
    monkeypatch.setattr(auth, "login_with_browser", lambda *a, **k: (_ for _ in ()).throw(loopback.LoginError("timed out waiting for the browser")))
    import storymap_gen.__main__ as cli
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    assert main(["login", "--client-id", CLIENT]) == 1
    err = capsys.readouterr().err
    assert "Sign-in did not complete: timed out" in err and "Unexpected" not in err
