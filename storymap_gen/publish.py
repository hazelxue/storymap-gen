"""Create a NEW private StoryMap draft item from compiled story JSON.

Mirrors what the Builder stores: an item of type 'StoryMap' with draft keywords and
the story JSON as a 'draft_<ts>.json' item resource. It never touches an existing
item. Authentication is supplied by the caller (token); credentials are never handled here.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import requests

BUILDER_VERSION = "26.37.1358"  # matches the Builder version seen on the reference story
SHARING = "https://www.arcgis.com/sharing/rest"


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


def _check(resp: requests.Response) -> dict:
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS REST error: {data['error']}")
    return data


def publish_draft(story: dict, title: str, username: str, token: str,
                  summary: str = "", sharing: str = SHARING) -> str:
    """Create the draft item and its draft resource. Returns the new item id."""
    params = build_item_params(story, title, summary)
    draft = params.pop("_draft_resource")
    params["token"] = token
    item = _check(requests.post(f"{sharing}/content/users/{username}/addItem", data=params, timeout=60))
    item_id = item["id"]
    files = {"file": (draft, json.dumps(story, ensure_ascii=False).encode("utf-8"), "application/json")}
    _check(requests.post(f"{sharing}/content/users/{username}/items/{item_id}/addResources",
                         data={"f": "json", "token": token, "resourcesPrefix": "", "fileName": draft},
                         files=files, timeout=60))
    return item_id
