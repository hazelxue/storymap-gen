"""Static wireframe previews of the three templates (the 'pick a direction first' step)."""
from __future__ import annotations

from html import escape

from .spec import StorySpec

_CSS = """body{font-family:system-ui,sans-serif;background:#f4f4f2;margin:24px}
.row{display:flex;gap:20px;flex-wrap:wrap}.card{background:#fff;border:1px solid #ccc;width:360px;padding:12px}
.frame{position:relative;height:200px;background:#cfd8dc;border:1px solid #90a4ae;overflow:hidden}
.map{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#546e7a;font-size:13px}
.panel{position:absolute;background:#263238;color:#fff;padding:8px;font-size:11px;line-height:1.3}
.docked{left:0;top:0;bottom:0;width:38%}.floating{left:10px;bottom:10px;width:50%;border-radius:6px}
.flow div{background:#fff;border:1px solid #b0bec5;margin:4px;padding:6px;font-size:11px}
.flow .m{background:#cfd8dc;height:50px;text-align:center;line-height:50px;color:#546e7a}
h3{margin:0 0 4px}small{color:#607d8b}"""


def render_preview(spec: StorySpec) -> str:
    sec = next((s for s in spec.sections if s.slides), spec.sections[0])
    slide = sec.slides[0] if sec.slides else None
    head = escape(slide.title if slide else sec.title)
    body = escape((slide.body if slide else sec.intro)[:110])
    cards = [
        ("docked-sidecar", "Text panel docked beside a full map",
         f'<div class="frame"><div class="map">web map</div><div class="panel docked"><b>{head}</b><br>{body}</div></div>'),
        ("floating-sidecar", "Full-bleed map with a floating text card",
         f'<div class="frame"><div class="map">web map</div><div class="panel floating"><b>{head}</b><br>{body}</div></div>'),
        ("longform", "Linear article: heading, text, inline map",
         f'<div class="frame flow"><div><b>{head}</b> {body[:60]}...</div><div class="m">web map</div></div>'),
    ]
    html = "".join(f'<div class="card"><h3>{n}</h3><small>{d}</small>{f}</div>' for n, d, f in cards)
    return (f"<!doctype html><meta charset=utf-8><style>{_CSS}</style>"
            f"<h2>{escape(spec.title)}: pick a layout</h2><div class=row>{html}</div>")
