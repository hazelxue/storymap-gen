"""Sign in to ArcGIS without passing a token through a chat window.

``storymap-gen login`` prints the standard OAuth consent URL, you approve it in your browser, then paste the address of the
page you land on (or just the token) into a hidden prompt in YOUR terminal. The token is checked against the portal and, unless
``--no-save`` is given, kept in a user-only file until it expires (about an hour). ``logout`` deletes it.

Nothing here ever sees a password: authorisation happens on the ArcGIS sign-in and consent pages.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

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
    if saved is None:
        return {"signed_in": False, "portal": base}
    return {"signed_in": True, "portal": base, "username": saved["username"],
            "minutes_left": max(0, int((saved["expires_at"] - time.time()) // 60))}


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


def save_token(token: str, username: str, portal: str, seconds: int | None) -> Path:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"token": token, "username": username, "portal": validate_portal(portal), "expires_at": time.time() + (seconds or 3300)}
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


def delete_token() -> bool:
    try:
        token_file().unlink()
        return True
    except OSError:
        return False


def resolve_credentials(portal: str = DEFAULT_PORTAL, username: str | None = None) -> tuple[str, str]:
    """Return ``(token, username)`` from ARCGIS_TOKEN or the saved login, or raise with instructions."""
    env = os.environ.get("ARCGIS_TOKEN")
    if env:
        if not username:
            raise ValueError("--username is required when the token comes from ARCGIS_TOKEN")
        return validate_token(env), username
    saved = load_token(portal)
    if saved:
        return saved["token"], username or saved["username"]
    raise ValueError("not signed in: run `storymap-gen login --client-id <id>` in your own terminal (see README: Publish), "
                     "or set ARCGIS_TOKEN")
