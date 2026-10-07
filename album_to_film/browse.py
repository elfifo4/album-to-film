"""Capture-order thumbnail index, for labelling chapters as a few ranges of numbers."""
import html
import json

import cv2

from . import catalog, paths
from .analyze import load_reduced

THUMB_W = 320
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Capture order</title>
<style>
  body {{ margin: 0; padding: 16px; background: #1c1b1a; color: #ece7df; font: 14px/1.4 -apple-system, sans-serif; }}
  h1 {{ font-size: 20px; }} h2 {{ font-size: 15px; margin: 28px 0 10px; color: #d8b98a; }}
  p {{ max-width: 70ch; color: #b9b2a6; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }}
  figure {{ margin: 0; background: #2a2826; border-radius: 6px; overflow: hidden; }}
  img {{ display: block; width: 100%; height: auto; }}
  figcaption {{ padding: 6px 8px; font-variant-numeric: tabular-nums; }}
  b {{ font-size: 16px; }} small {{ color: #9c958a; display: block; }}
  .flag {{ color: #e0a060; }}
</style></head><body>
<h1>Capture order &mdash; {count} photographs</h1>
<p>The bold number is <code>capture_order</code>. To label chapters, note the numbers where
one chapter ends and the next begins, then fill the ranges in <code>editorial/event_ranges.json</code>.</p>
{sections}
</body></html>"""


def run() -> None:
    con = catalog.connect()
    rows = con.execute(
        "SELECT p.*, m.data, (SELECT COUNT(*) FROM photos e WHERE e.variant_group=p.variant_group "
        "AND e.role='edited') AS has_edit FROM photos p LEFT JOIN metrics m ON m.photo_id=p.id "
        "WHERE p.role='original' ORDER BY p.capture_order"
    ).fetchall()
    con.close()
    thumbs = paths.assert_not_in_source(paths.PREVIEWS_DIR / "browse" / "thumbs")
    thumbs.mkdir(parents=True, exist_ok=True)

    made, sections, current = 0, [], None
    for r in rows:
        out = thumbs / f"{r['id']}.jpg"
        if not out.exists():
            img = load_reduced(paths.SOURCE_DIR / r["filename"], 8)
            scale = THUMB_W / img.shape[1]
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tofile(out)
            made += 1
        if r["segment"] != current:
            if current is not None:
                sections.append("</div>")
            current = r["segment"]
            sections.append(f"<h2>Capture segment {current}</h2><div class='grid'>")
        flags = " ".join(json.loads(r["data"])["flags"]) if r["data"] else ""
        notes = ("edited variant exists" if r["has_edit"] else "")
        sections.append(
            f"<figure><img loading='lazy' src='thumbs/{html.escape(out.name)}' alt=''>"
            f"<figcaption><b>{r['capture_order']}</b> {(r['filename_time'] or '')[11:]}"
            f"<small>{html.escape(r['filename'])}</small><small>{notes}</small>"
            f"<small class='flag'>{html.escape(flags)}</small></figcaption></figure>"
        )
    sections.append("</div>")
    catalog.write_text_atomic(paths.PREVIEWS_DIR / "browse" / "index.html",
                              PAGE.format(count=len(rows), sections="\n".join(sections)))
    print(f"browse: {len(rows)} photos, {made} new thumbnails -> previews/browse/index.html")
