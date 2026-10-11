"""A tiny on-disk cache so repeated runs do not hit public servers again (their usage policies ask for this)."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path


def cache_dir() -> Path:
    override = os.environ.get("STORYMAP_CACHE_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "storymap-gen" / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "storymap-gen"


def _path(key: str) -> Path:
    return cache_dir() / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".bin")


def get(key: str, ttl_seconds: float) -> bytes | None:
    p = _path(key)
    try:
        if p.is_file() and time.time() - p.stat().st_mtime <= ttl_seconds:
            return p.read_bytes()
    except OSError:
        return None
    return None


def put(key: str, data: bytes) -> None:
    try:
        p = _path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    except OSError:
        pass  # a cache that cannot be written is not an error
