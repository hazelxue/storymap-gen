"""Sign in to ArcGIS without passing a token through a chat window.

``storymap-gen login`` opens the ArcGIS sign-in page in your browser. You click Allow; ArcGIS sends the browser back to a one-shot
listener on your own computer (``loopback.py``), which receives a one-time code and exchanges it (with PKCE) for an access token.
Nothing is copied or typed. ``login --manual`` is the fallback for a computer without a browser: you open the printed address
yourself and paste the address of the page you land on into a hidden prompt in YOUR terminal.

The token is checked against the portal and kept in a user-only file until it expires (about half an hour to an hour); with
``--stay-signed-in`` a refresh token is kept as well, so the sign-in renews itself for about two weeks. ``logout`` deletes the
file (and revokes the refresh token). Nothing here ever sees a password: authorisation happens on the ArcGIS pages.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from . import loopback
from .publish import _post, validate_portal, validate_token

DEFAULT_PORTAL = "https://www.arcgis.com/sharing/rest"
DEFAULT_REDIRECT = "https://localhost:8765/storymap-gen-callback"
CLIENT_ID = re.compile(r"^[A-Za-z0-9]{8,64}$")


def config_dir() -> Path:
    override = os.environ.get("STORYMAP_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "storymap-gen"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "storymap-gen"


def token_file() -> Path:
    return config_dir() / "token.json"


def normalize_portal(url: str) -> str:
    """Accept an organisation address in any common form ('myorg.maps.arcgis.com', 'https://x.maps.arcgis.com/') and return the
    REST address the tool needs. Validation of the host still happens in ``validate_portal``."""
    url = url.strip()
    if not url:
        return DEFAULT_PORTAL
    if "://" not in url:
        url = "https://" + url
    url = url.rstrip("/")
    return url if url.endswith("/sharing/rest") else url + "/sharing/rest"


def profile_file() -> Path:
    return config_dir() / "profile.json"


def save_profile(client_id: str, portal: str) -> None:
    """Remember the (non-secret) client id and organisation, so the next login needs no options."""
    path = profile_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"client_id": client_id, "portal": validate_portal(portal)}), encoding="utf-8")


def load_profile() -> dict[str, str]:
    try:
        data = json.loads(profile_file().read_text(encoding="utf-8"))
        client_id, portal = str(data.get("client_id", "")), validate_portal(str(data.get("portal", "")))
        return {"client_id": client_id, "portal": portal} if CLIENT_ID.match(client_id) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def resolve_portal(given: str | None = None) -> str:
    """The portal to use: the one given, else STORYMAP_PORTAL, else the one remembered from the last login, else ArcGIS Online."""
    chosen = given or os.environ.get("STORYMAP_PORTAL") or load_profile().get("portal") or DEFAULT_PORTAL
    return validate_portal(normalize_portal(chosen))


def status(portal: str | None = None) -> dict[str, Any]:
    """Whether a saved login is usable (never includes the token itself)."""
    base = resolve_portal(portal)
    saved = load_token(base)
    raw = _load_raw(base) or {}
    renews = bool(raw.get("refresh_token")) and raw.get("refresh_expires_at", 0) > time.time() + 60
    if saved is None and not renews:
        return {"signed_in": False, "portal": base}
    out: dict[str, Any] = {"signed_in": True, "portal": base, "username": str((saved or raw).get("username", "")),
                           "minutes_left": max(0, int(((saved or {}).get("expires_at", 0) - time.time()) // 60)), "renews": renews}
    if renews:
        out["days_left"] = max(0, int((raw["refresh_expires_at"] - time.time()) // 86400))
    return out


def authorize_url(client_id: str, portal: str = DEFAULT_PORTAL, redirect_uri: str = DEFAULT_REDIRECT, minutes: int = 60) -> str:
    if not CLIENT_ID.match(client_id):
        raise ValueError("the client id looks malformed (it is shown on the OAuth credentials item in your organisation)")
    base = validate_portal(portal)
    return (f"{base}/oauth2/authorize?client_id={client_id}&response_type=token&expiration={int(minutes)}"
            f"&redirect_uri={quote(redirect_uri, safe='')}")


def parse_token(text: str) -> tuple[str, int | None]:
    """Accept a raw token, or the URL / fragment of the redirect page; return ``(token, seconds_valid or None)``."""
    text = text.strip()
    if "access_token=" in text:
        frag = urlsplit(text).fragment or text.split("#", 1)[-1]
        params = parse_qs(frag if "access_token=" in frag else text.split("?", 1)[-1])
        token = params.get("access_token", [""])[0]
        exp = params.get("expires_in", [""])[0]
        return validate_token(token), int(exp) if exp.isdigit() else None
    return validate_token(text), None


def verify_token(token: str, portal: str = DEFAULT_PORTAL) -> dict[str, Any]:
    """Ask the portal who the token belongs to. Raises ``RuntimeError`` if it is not valid."""
    data = _post(f"{validate_portal(portal)}/community/self", data={"f": "json", "token": token})
    username = str(data.get("username", ""))
    if not username:
        raise RuntimeError("the portal did not recognise this token")
    return {"username": username, "fullName": str(data.get("fullName", ""))}


def authorize_url_pkce(client_id: str, portal: str, redirect_uri: str, challenge: str, state: str) -> str:
    """The sign-in address for the authorization-code flow with PKCE (the recommended flow for apps without a secret)."""
    if not CLIENT_ID.match(client_id):
        raise ValueError("the client id looks malformed (it is shown on the OAuth credentials item in your organisation)")
    return (f"{validate_portal(portal)}/oauth2/authorize?client_id={client_id}&response_type=code&redirect_uri={quote(redirect_uri, safe='')}"
            f"&code_challenge={challenge}&code_challenge_method=S256&state={state}")


def exchange_code(client_id: str, portal: str, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
    """Trade the one-time code (and the PKCE verifier only this computer knows) for the access token."""
    data = _post(f"{validate_portal(portal)}/oauth2/token", data={
        "f": "json", "client_id": client_id, "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        "redirect_uri": redirect_uri})
    validate_token(str(data.get("access_token", "")))
    return data


def refresh_access_token(client_id: str, portal: str, refresh_token: str) -> dict[str, Any]:
    data = _post(f"{validate_portal(portal)}/oauth2/token", data={
        "f": "json", "client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token})
    validate_token(str(data.get("access_token", "")))
    return data


def save_token(token: str, username: str, portal: str, seconds: int | None, refresh_token: str | None = None,
               refresh_seconds: int | None = None, client_id: str | None = None) -> Path:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"token": token, "username": username, "portal": validate_portal(portal),
                               "expires_at": time.time() + (seconds or 3300)}
    if refresh_token and client_id and CLIENT_ID.match(client_id):
        payload.update({"refresh_token": validate_token(refresh_token), "client_id": client_id,
                        "refresh_expires_at": time.time() + (refresh_seconds or 13 * 24 * 3600)})
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # not supported on every filesystem; the file lives in your own profile directory
    return path


def load_token(portal: str = DEFAULT_PORTAL) -> dict[str, Any] | None:
    """The saved token for this portal, or ``None`` if there is none or it has expired."""
    try:
        data = json.loads(token_file().read_text(encoding="utf-8"))
        if data["expires_at"] <= time.time() + 30 or data["portal"] != validate_portal(portal):
            return None
        validate_token(data["token"])
        return {"token": data["token"], "username": data["username"], "expires_at": data["expires_at"]}
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _load_raw(portal: str) -> dict[str, Any] | None:
    try:
        data = json.loads(token_file().read_text(encoding="utf-8"))
        return data if data.get("portal") == validate_portal(portal) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def refreshed(portal: str = DEFAULT_PORTAL) -> dict[str, Any] | None:
    """A new access token from the saved refresh token (and saves it), or ``None`` if the login cannot renew itself."""
    saved = _load_raw(portal)
    try:
        if not saved or not saved.get("refresh_token") or saved["refresh_expires_at"] <= time.time() + 60:
            return None
        data = refresh_access_token(str(saved["client_id"]), portal, str(saved["refresh_token"]))
    except (KeyError, TypeError, ValueError, RuntimeError):
        return None
    seconds = int(data.get("expires_in") or 1800)
    save_token(data["access_token"], str(saved["username"]), portal, seconds, str(data.get("refresh_token") or saved["refresh_token"]),
               saved["refresh_expires_at"] - time.time(), str(saved["client_id"]))
    return {"token": data["access_token"], "username": str(saved["username"]), "expires_at": time.time() + seconds}


def delete_token() -> bool:
    """Remove the saved login. A saved refresh token is revoked at ArcGIS first (best effort), so it cannot be used after logout."""
    saved = None
    try:
        saved = json.loads(token_file().read_text(encoding="utf-8"))
        if saved.get("refresh_token") and saved.get("client_id"):
            _post(f"{validate_portal(saved['portal'])}/oauth2/revokeToken", data={
                "f": "json", "client_id": saved["client_id"], "auth_token": saved["refresh_token"], "token_type_hint": "refresh_token"})
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        pass                                                      # revoking is best effort; the local copy is removed regardless
    try:
        token_file().unlink()
        return True
    except OSError:
        return False


def login_with_browser(client_id: str, portal: str, port: int, stay_signed_in: bool, open_url: Callable[[str], Any],
                       timeout: float = loopback.TIMEOUT_SECONDS, save: bool = True) -> dict[str, Any]:
    """The whole sign-in: open the browser, wait for the one-time code on this computer, exchange it, verify, save. No copying."""
    verifier, challenge = loopback.pkce_pair()
    state = loopback.new_state()
    uri = loopback.redirect_uri(port)
    url = authorize_url_pkce(client_id, portal, uri, challenge, state)
    code = loopback.wait_for_code(state, port, timeout, on_ready=lambda: open_url(url))
    data = exchange_code(client_id, portal, code, verifier, uri)
    who = verify_token(data["access_token"], portal)
    if save:
        keep = stay_signed_in and bool(data.get("refresh_token"))
        save_token(data["access_token"], who["username"], portal, int(data.get("expires_in") or 1800),
                   str(data["refresh_token"]) if keep else None, int(data.get("refresh_token_expires_in") or 0) or None, client_id)
        who["stays_signed_in"] = keep
    return who


def resolve_credentials(portal: str = DEFAULT_PORTAL, username: str | None = None) -> tuple[str, str]:
    """Return ``(token, username)`` from ARCGIS_TOKEN or the saved login, or raise with instructions."""
    env = os.environ.get("ARCGIS_TOKEN")
    if env:
        if not username:
            raise ValueError("--username is required when the token comes from ARCGIS_TOKEN")
        return validate_token(env), username
    saved = load_token(portal) or refreshed(portal)
    if saved:
        return saved["token"], username or saved["username"]
    raise ValueError("not signed in: run `storymap-gen login` in your own terminal (the first time: `storymap-gen login --client-id <id> "
                     "--portal <your-organisation>.maps.arcgis.com`), or set ARCGIS_TOKEN")
