"""Static before/after review page for the pilot."""
import html

from . import catalog, paths

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pilot review</title>
<style>
  :root {{ --bg:#1c1b1a; --card:#272523; --ink:#ece7df; --dim:#a39c90; --accent:#d8b98a;
          --high:#5ac85a; --medium:#ebc83c; --low:#e65046; }}
  body {{ margin:0; padding:16px; background:var(--bg); color:var(--ink); font:14px/1.45 -apple-system, sans-serif; }}
  h1 {{ font-size:20px; margin:0 0 4px; }} h2 {{ font-size:16px; margin:0 0 10px; }}
  p.lead {{ color:var(--dim); max-width:80ch; margin:0 0 20px; }}
  section {{ background:var(--card); border-radius:8px; padding:14px; margin:0 0 18px; }}
  .shots {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:10px; align-items:start; }}
  figure {{ margin:0; }} figure img {{ display:block; width:100%; height:auto; max-height:70vh; object-fit:contain; background:#111; border-radius:4px; }}
  figcaption {{ color:var(--dim); padding-top:4px; font-size:12px; }}
  dl {{ display:grid; grid-template-columns:max-content 1fr; gap:2px 14px; margin:12px 0 0; }}
  dt {{ color:var(--dim); }} dd {{ margin:0; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }}
  .tag {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; color:#111; font-weight:600; }}
  .high {{ background:var(--high); }} .medium {{ background:var(--medium); }} .low {{ background:var(--low); color:#fff; }}
  .event {{ background:var(--accent); }} .flag {{ color:#e0a060; }}
</style></head><body>
<h1>Pilot review &mdash; {count} files</h1>
<p class="lead">Left to right: the capture with the detected outline, geometry only (crop, perspective, rotation),
the <b>light</b> enhancement (lighting evened out, white balance from the paper, gentle levels), and the
<b>standard</b> enhancement (adds cast correction, local contrast, sharpening). Click an image to open it.</p>
{sections}
</body></html>"""


def _figure(pid: str, kind: str, caption: str) -> str:
    src = f"../../previews/pilot/{pid}_{kind}.jpg"
    return f"<figure><a href='{src}'><img loading='lazy' src='{src}' alt=''></a><figcaption>{caption}</figcaption></figure>"


def _section(r: dict) -> str:
    d, o, e = r["detect"], r["orientation"], r["enhancement"]
    parts = ", ".join(f"{k} {v}" for k, v in d["parts"].items()) or d["status"]
    trims = ", ".join(f"{k} {v:.1%}" for k, v in d["trims"].items() if v) or "none"
    faces = o["faces"]
    faces_txt = (f"suggest {faces['rotation_cw_deg']}&deg; ({faces['faces']} faces, margin {faces['margin']}"
                 f"{', decisive' if faces['decisive'] else ', not decisive'})") if faces else "not run"
    lv = e["light"]
    rows = [
        ("Crop", f"<span class='tag {d['level']}'>{d['level']} {d['confidence']:.2f}</span> "
                 f"{'applied' if d['cropped'] else 'not applied, whole frame kept'}"),
        ("Crop evidence", html.escape(parts)),
        ("Inward trim", trims),
        ("Rotation", f"{o['rotation_cw_deg']}&deg; clockwise &mdash; from {o['source']}, confidence {o['confidence']}"),
        ("Faces check", faces_txt),
        ("Enhancement", f"saved as <b>{e['chosen']}</b>; levels gain {lv.get('gain')}, "
                        f"black {lv.get('black')}, white {lv.get('white')}"),
        ("Edited variant", r["variant_kind"] or "none"),
        ("Output", f"{r['output']['width']}&times;{r['output']['height']} &rarr; {html.escape(r['output']['processed'])}"),
        ("Warnings", f"<span class='flag'>{html.escape(', '.join(r['flags']))}</span>" if r["flags"] else "none"),
    ]
    details = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    return (
        f"<section><h2>#{r['capture_order']} &nbsp; {html.escape(r['filename'])} &nbsp; "
        f"<span class='tag event'>{r.get('category', 'uncertain')}</span></h2>"
        f"<div class='shots'>{_figure(r['id'], 'overlay', 'capture + detected outline')}"
        f"{_figure(r['id'], 'geom', 'geometry only')}{_figure(r['id'], 'light', 'light')}"
        f"{_figure(r['id'], 'standard', 'standard')}</div><dl>{details}</dl></section>"
    )


def build(results: list[dict]) -> None:
    body = "\n".join(_section(r) for r in sorted(results, key=lambda r: (r["capture_order"], r["role"] != "original")))
    catalog.write_text_atomic(paths.REVIEW_DIR / "pilot" / "index.html", PAGE.format(count=len(results), sections=body))
