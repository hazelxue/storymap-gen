"""A self-contained data explorer: one HTML file that shows the fetched layers on a map before any story is written.

It needs no network and no map service: the layers are drawn on a canvas from the data embedded in the page. The reader can
switch layers on and off, zoom, pan, and click a feature to see its attributes. The counts beside each layer are the same
counts the claim checker will use, so what the user sees here is what they can write about.
"""
from __future__ import annotations

import json
import math
from typing import Any

import geopandas as gpd

from .geo import DataStore
from .webmap import STYLES, layer_role, layer_title

MAX_FEATURES = 12_000          # per layer drawn in the explorer
MAX_ATTRS = 8
MAX_VALUE_CHARS = 80
SIMPLIFY_DEG = 0.00001         # about one metre
MAX_PAGE_BYTES = 14 * 1024 * 1024


def _coords(geom: Any) -> Any:
    t = geom.geom_type
    r = lambda c: [round(c[0], 5), round(c[1], 5)]  # noqa: E731
    if t == "Point":
        return r(geom.coords[0])
    if t == "LineString":
        return [r(c) for c in geom.coords]
    if t == "Polygon":
        return [[r(c) for c in ring.coords] for ring in [geom.exterior, *geom.interiors]]
    return [_coords(g) for g in geom.geoms]


def _value(v: Any) -> Any:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    if hasattr(v, "item"):
        v = v.item()
    return v if isinstance(v, (int, float, bool)) else str(v)[:MAX_VALUE_CHARS]


def layer_payload(name: str, gdf: gpd.GeoDataFrame) -> dict[str, Any]:
    gdf = gdf.to_crs(4326)
    attrs = [c for c in gdf.columns if c != gdf.geometry.name][:MAX_ATTRS]
    feats = []
    total = len(gdf)
    step = max(1, math.ceil(total / MAX_FEATURES))
    for i, (_, row) in enumerate(gdf.iterrows()):
        if i % step:
            continue
        geom = row.geometry
        if geom is None or not hasattr(geom, "geom_type") or geom.is_empty:
            continue
        if geom.geom_type != "Point":
            geom = geom.simplify(SIMPLIFY_DEG, preserve_topology=True)
        feats.append({"t": geom.geom_type, "c": _coords(geom), "p": {a: _value(row[a]) for a in attrs if _value(row[a]) is not None}})
    style = {**STYLES["default"], **STYLES.get(layer_role(name), {})}
    return {"name": name, "title": layer_title(name), "count": total, "shown": len(feats), "style": style, "features": feats}


def build_explorer_html(store: DataStore, title: str = "Data explorer") -> str:
    layers = [p for p in (layer_payload(n, g) for n, g in store.layers.items() if len(g)) if p["features"]]
    payload = json.dumps({"title": title, "layers": layers}, ensure_ascii=False, separators=(",", ":"))
    # keep the data from ever closing the script element or starting an HTML comment
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = _TEMPLATE.replace("__DATA__", payload)
    if len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ValueError("the explorer page would be larger than 14 MB; fetch a smaller area or fewer layers")
    return html


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
<title>Data explorer</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px/1.4 system-ui,Segoe UI,sans-serif;color:#222;display:flex;height:100vh}
#side{width:300px;padding:14px;overflow:auto;border-right:1px solid #ddd;background:#fafafa}
h1{font-size:16px;margin:0 0 4px}p.sub{margin:0 0 12px;color:#666;font-size:12px}
label{display:flex;gap:8px;align-items:center;padding:5px 0;cursor:pointer}.sw{width:14px;height:14px;border-radius:3px;flex:none;border:1px solid #0003}
.n{margin-left:auto;color:#666;font-variant-numeric:tabular-nums}#info{margin-top:14px;font-size:12px;border-top:1px solid #ddd;padding-top:10px}
#info b{display:block;margin-bottom:4px}#info div{display:flex;gap:6px}#info span:first-child{color:#777;min-width:90px}
#map{flex:1;position:relative;background:#f4f2ee}canvas{position:absolute;inset:0;width:100%;height:100%}
#hint{position:absolute;right:10px;bottom:8px;font-size:11px;color:#777;background:#fffc;padding:2px 6px;border-radius:4px}
@media(max-width:700px){body{flex-direction:column}#side{width:auto;height:38vh;border-right:0;border-bottom:1px solid #ddd}}
</style></head><body>
<div id="side"><h1 id="t"></h1><p class="sub">Counts are the real feature counts. Click a feature to see its attributes.</p><div id="layers"></div><div id="info"></div></div>
<div id="map"><canvas id="c"></canvas><div id="hint">scroll to zoom, drag to pan</div></div>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
const D=JSON.parse(document.getElementById("data").textContent);
document.title=D.title;document.getElementById("t").textContent=D.title;
const cv=document.getElementById("c"),ctx=cv.getContext("2d");
const mx=lon=>lon*Math.PI/180*6378137, my=lat=>Math.log(Math.tan(Math.PI/4+lat*Math.PI/360))*6378137;
const proj=c=>[mx(c[0]),my(c[1])];
let minx=1e99,miny=1e99,maxx=-1e99,maxy=-1e99;
const visit=(t,c,f)=>{if(t==="Point")f(c);else if(t==="LineString")c.forEach(f);else if(t==="Polygon")c.forEach(r=>r.forEach(f));else c.forEach(g=>visit(t==="MultiPolygon"?"Polygon":t==="MultiLineString"?"LineString":"Point",g,f))};
D.layers.forEach(L=>{L.on=true;L.features.forEach(F=>{F.pc=null;visit(F.t,F.c,p=>{const q=proj(p);minx=Math.min(minx,q[0]);maxx=Math.max(maxx,q[0]);miny=Math.min(miny,q[1]);maxy=Math.max(maxy,q[1])})})});
let sc=1,ox=0,oy=0;
const rgba=a=>"rgba("+a[0]+","+a[1]+","+a[2]+","+(a[3]/255)+")";
function fit(){const w=cv.clientWidth,h=cv.clientHeight;sc=Math.min(w/(maxx-minx||1),h/(maxy-miny||1))*0.92;ox=(w-(maxx-minx)*sc)/2-minx*sc;oy=(h+(maxy-miny)*sc)/2+miny*sc}
const X=p=>p[0]*sc+ox,Y=p=>oy-p[1]*sc;
function ring(r){r.forEach((p,i)=>{const q=proj(p);i?ctx.lineTo(X(q),Y(q)):ctx.moveTo(X(q),Y(q))})}
function draw(){const d=devicePixelRatio||1;cv.width=cv.clientWidth*d;cv.height=cv.clientHeight*d;ctx.setTransform(d,0,0,d,0,0);ctx.clearRect(0,0,cv.clientWidth,cv.clientHeight);
 const order={Polygon:0,MultiPolygon:0,LineString:1,MultiLineString:1,Point:2};
 D.layers.filter(L=>L.on).sort((a,b)=>order[a.features[0].t]-order[b.features[0].t]).forEach(L=>{const s=L.style;
  L.features.forEach(F=>{const parts=F.t==="MultiPolygon"?F.c.map(c=>["Polygon",c]):F.t==="MultiLineString"?F.c.map(c=>["LineString",c]):[[F.t,F.c]];
   parts.forEach(([t,c])=>{ctx.beginPath();
    if(t==="Point"){const q=proj(c);ctx.arc(X(q),Y(q),Math.max(2.5,(s.size||6)/2),0,6.283);ctx.fillStyle=rgba(s.color||[90,60,140,255]);ctx.fill();ctx.strokeStyle="#fff";ctx.lineWidth=1;ctx.stroke()}
    else if(t==="LineString"){ring(c);ctx.strokeStyle=rgba(s.color||[110,110,110,255]);ctx.lineWidth=Math.max(0.7,s.width||1);ctx.stroke()}
    else{c.forEach(ring);ctx.fillStyle=rgba(s.fill||[150,120,200,140]);ctx.fill("evenodd");ctx.strokeStyle=rgba(s.outline||[90,60,140,255]);ctx.lineWidth=s.width||1;ctx.stroke()}})})})}
const box=document.getElementById("layers");
D.layers.forEach(L=>{const l=document.createElement("label"),c=document.createElement("input"),w=document.createElement("span"),n=document.createElement("span"),tx=document.createElement("span");
 c.type="checkbox";c.checked=true;c.onchange=()=>{L.on=c.checked;draw()};
 const s=L.style,poly=L.features[0].t.includes("Polygon"),col=poly?(s.fill&&s.fill[3]>0?s.fill:s.outline):(s.color||[90,90,90,255]);w.className="sw";w.style.background=rgba(col);
 tx.textContent=L.title;n.className="n";n.textContent=L.count.toLocaleString()+(L.shown<L.count?" (drawing "+L.shown.toLocaleString()+")":"");
 l.append(c,w,tx,n);box.appendChild(l)});
let drag=null;
cv.onwheel=e=>{e.preventDefault();const f=Math.exp(-e.deltaY*0.0015),r=cv.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;ox=x-(x-ox)*f;oy=y-(y-oy)*f;sc*=f;draw()};
cv.onmousedown=e=>{drag=[e.clientX,e.clientY,ox,oy,false]};
addEventListener("mouseup",e=>{if(drag&&!drag[4])pick(e);drag=null});
addEventListener("mousemove",e=>{if(!drag)return;const dx=e.clientX-drag[0],dy=e.clientY-drag[1];if(Math.abs(dx)+Math.abs(dy)>3)drag[4]=true;if(drag[4]){ox=drag[2]+dx;oy=drag[3]+dy;draw()}});
function inside(r,x,y){let k=false;const q=r.map(p=>{const z=proj(p);return[X(z),Y(z)]});for(let i=0,j=q.length-1;i<q.length;j=i++){if((q[i][1]>y)!==(q[j][1]>y)&&x<(q[j][0]-q[i][0])*(y-q[i][1])/(q[j][1]-q[i][1])+q[i][0])k=!k}return k}
function pick(e){const r=cv.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;let best=null,bd=10;
 D.layers.filter(L=>L.on).forEach(L=>L.features.forEach(F=>{let d=1e9;const pts=[];visit(F.t,F.c,p=>pts.push(p));
  for(const p of pts){const q=proj(p);d=Math.min(d,Math.hypot(X(q)-x,Y(q)-y))}
  if(F.t.includes("Polygon")){const polys=F.t==="MultiPolygon"?F.c:[F.c];if(polys.some(pg=>inside(pg[0],x,y)))d=Math.min(d,6)}
  if(d<bd){bd=d;best=[L,F]}}));
 const info=document.getElementById("info");info.textContent="";
 if(!best){return}
 const b=document.createElement("b");b.textContent=best[0].title;info.appendChild(b);
 Object.entries(best[1].p).forEach(([k,v])=>{const row=document.createElement("div"),a=document.createElement("span"),c=document.createElement("span");a.textContent=k;c.textContent=String(v);row.append(a,c);info.appendChild(row)})}
addEventListener("resize",()=>{fit();draw()});fit();draw();
})();
</script></body></html>
"""
