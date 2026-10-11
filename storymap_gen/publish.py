"""Create NEW private items (StoryMap drafts, Web Maps) in an ArcGIS account.

Mirrors what the Builder stores: an item of type 'StoryMap' with draft keywords and the story JSON as a
'draft_<ts>.json' item resource. It never touches an existing item, and the token is supplied by the caller.

Security: the token is sent only to a validated ArcGIS portal (https, ``*.arcgis.com`` or a host you list in
``STORYMAP_ALLOWED_PORTAL_HOSTS`` for ArcGIS Enterprise), redirects are never followed (a 307/308 would re-send the body,
token included, to another host), and the user name cannot alter the request path.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import quote, urlsplit

import requests

BUILDER_VERSION = "26.37.1358"  # matches the Builder version seen on the reference story
SHARING = "https://www.arcgis.com/sharing/rest"
USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
TIMEOUT = 60


def validate_portal(url: str) -> str:
    """Return a normalised portal REST URL, or raise ``ValueError`` if a token must not be sent there."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    extra = {h.strip().lower() for h in os.environ.get("STORYMAP_ALLOWED_PORTAL_HOSTS", "").split(",") if h.strip()}
    if parts.scheme != "https":
        raise ValueError("the portal URL must use https")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("the portal URL must not contain credentials, a query or a fragment")
    if parts.port not in (None, 443):
        raise ValueError("the portal URL must use the default https port")
    if not (host == "arcgis.com" or host.endswith(".arcgis.com") or host in extra):
        raise ValueError(f"refusing to send credentials to '{host}': not an ArcGIS Online host "
                         "(for ArcGIS Enterprise set STORYMAP_ALLOWED_PORTAL_HOSTS)")
    path = parts.path.rstrip("/")
    if not path.endswith("/sharing/rest"):
        raise ValueError("the portal URL must end with /sharing/rest")
    return f"https://{host}{path}"


def validate_username(username: str) -> str:
    if not USERNAME.match(username):
        raise ValueError("invalid ArcGIS user name")
    return quote(username, safe="")


def validate_token(token: str) -> str:
    if not token or len(token) > 4096 or re.search(r"\s", token):
        raise ValueError("the token looks malformed")
    return token


def draft_resource_name(ts_ms: Optional[int] = None) -> str:
    return f"draft_{ts_ms or int(time.time() * 1000)}.json"


def build_item_params(story: dict, title: str, summary: str = "", ts_ms: Optional[int] = None) -> dict:
    """addItem form fields (without token). Always private."""
    draft = draft_resource_name(ts_ms)
    return {
        "title": title,
        "type": "StoryMap",
        "snippet": summary[:250],
        "access": "private",
        "typeKeywords": ",".join([
            "arcgis-storymaps", "StoryMap", "Web Application", "smstatusdraft",
            f"smversiondraft:{BUILDER_VERSION}", f"smdraftresourceid:{draft}"]),
        "text": json.dumps(story, ensure_ascii=False),
        "f": "json",
        "_draft_resource": draft,  # consumed by publish_draft, not sent to the API
    }


def _post(url: str, **kwargs: Any) -> dict[str, Any]:
    """POST without following redirects and return the JSON body, raising a clear error otherwise."""
    resp = requests.post(url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
    if 300 <= resp.status_code < 400:
        raise RuntimeError("the portal answered with a redirect; refusing to re-send credentials")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"the portal returned a non-JSON response (HTTP {resp.status_code})") from None
    if "error" in data:
        err = data["error"]
        raise RuntimeError(f"ArcGIS REST error {err.get('code', '')}: {str(err.get('message', err))[:200]}"
                           if isinstance(err, dict) else "ArcGIS REST error")
    return data


def publish_draft(story: dict, title: str, username: str, token: str,
                  summary: str = "", sharing: str = SHARING) -> str:
    """Create the draft item and its draft resource. Returns the new item id."""
    base = validate_portal(sharing)
    user = validate_username(username)
    token = validate_token(token)
    params = build_item_params(story, title, summary)
    draft = params.pop("_draft_resource")
    params["token"] = token
    item = _post(f"{base}/content/users/{user}/addItem", data=params)
    item_id = str(item["id"])
    if not re.fullmatch(r"[0-9a-f]{32}", item_id):
        raise RuntimeError("the portal returned an unexpected item id")
    files = {"file": (draft, json.dumps(story, ensure_ascii=False).encode("utf-8"), "application/json")}
    _post(f"{base}/content/users/{user}/items/{item_id}/addResources",
          data={"f": "json", "token": token, "resourcesPrefix": "", "fileName": draft}, files=files)
    return item_id


def build_webmap_item_params(webmap: dict, title: str, extent_lonlat: tuple[float, float, float, float], summary: str = "") -> dict:
    """addItem form fields for a generated Web Map (without token). Always private."""
    xmin, ymin, xmax, ymax = extent_lonlat
    return {
        "title": title,
        "type": "Web Map",
        "snippet": summary[:250],
        "access": "private",
        "tags": "storymap-gen",
        "typeKeywords": "ArcGIS Online,Explorer Web Map,Map,Online Map,Web Map",
        "extent": f"{xmin:.6f},{ymin:.6f},{xmax:.6f},{ymax:.6f}",
        "text": json.dumps(webmap, ensure_ascii=False),
        "f": "json",
    }


def publish_web_map(webmap: dict, title: str, username: str, token: str, extent_lonlat: tuple[float, float, float, float],
                    summary: str = "", sharing: str = SHARING) -> str:
    """Create a NEW private Web Map item and return its id."""
    base = validate_portal(sharing)
    user = validate_username(username)
    params = build_webmap_item_params(webmap, title, extent_lonlat, summary)
    params["token"] = validate_token(token)
    item = _post(f"{base}/content/users/{user}/addItem", data=params)
    item_id = str(item.get("id", ""))
    if not re.fullmatch(r"[0-9a-f]{32}", item_id):
        raise RuntimeError("the portal returned an unexpected item id")
    return item_id
