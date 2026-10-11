"""Pictures for a story: download them (only when the user agrees), check what they really are, and describe them for the Builder.

The StoryMaps Builder does not load pictures from a web address; a picture has to be a resource stored on the story item. So when
the user publishes with ``--download-images``, each https picture named in the spec is fetched here, checked, and later uploaded
to the draft. Every fetch goes through the same guarded client as the other downloads (https only, public addresses only, no
redirects, size limit) and the bytes must be a real JPEG, PNG, GIF or WebP: the file is identified by its first bytes, never by
its name or by what the server says it is.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import net
from .spec import ImageBlock, StorySpec

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 30
TYPES = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}


@dataclass(frozen=True)
class Picture:
    url: str
    name: str            # resource name on the story item, e.g. img-0123456789ab.jpg
    content_type: str
    width: int
    height: int
    data: bytes


def spec_image_urls(spec: StorySpec) -> list[str]:
    """Every picture the spec refers to, once each, in reading order."""
    urls = [spec.cover_image_url] if spec.cover_image_url else []
    for sec in spec.sections:
        if sec.image_url:
            urls.append(sec.image_url)
        urls += [b.url for b in sec.blocks if isinstance(b, ImageBlock)]
    seen: dict[str, None] = {}
    for u in urls:
        seen.setdefault(u, None)
    return list(seen)


def sniff(data: bytes) -> tuple[str, int, int]:
    """(extension, width, height) of a JPEG, PNG, GIF or WebP, from its bytes. Raises ``ValueError`` for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return "gif", w, h
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            return "webp", 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            w, h = struct.unpack("<HH", data[26:30])
            return "webp", w & 0x3FFF, h & 0x3FFF
        if kind == b"VP8L" and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            return "webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        raise ValueError("unsupported WebP variant")
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpg", w, h
            i += 2 + length
        raise ValueError("could not read the JPEG size")
    raise ValueError("not a JPEG, PNG, GIF or WebP picture")


def fetch_picture(url: str) -> Picture:
    """Download one picture and check it. Raises ``net.NetworkError`` or ``ValueError`` with a message for the user."""
    data = net.request_bytes("GET", url, max_bytes=MAX_IMAGE_BYTES, headers={"Accept": "image/*"})
    try:
        ext, width, height = sniff(data)
    except ValueError as e:
        raise ValueError(f"{urlsplit(url).hostname}: {e}") from None
    if not (0 < width <= 20_000 and 0 < height <= 20_000):
        raise ValueError(f"{urlsplit(url).hostname}: the picture size looks wrong")
    name = "img-" + hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:12] + "." + ext
    return Picture(url, name, TYPES[ext], width, height, data)


def fetch_pictures(urls: list[str]) -> tuple[dict[str, Picture], list[str]]:
    """All pictures that could be fetched, and a message for each one that could not."""
    if len(urls) > MAX_IMAGES:
        raise ValueError(f"a story may use at most {MAX_IMAGES} pictures")
    got: dict[str, Picture] = {}
    failed: list[str] = []
    for url in urls:
        try:
            got[url] = fetch_picture(url)
        except (net.NetworkError, ValueError) as e:
            failed.append(f"{url[:80]}: {e}")
    return got, failed
