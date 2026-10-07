"""Pilot preparation: choose a small representative set from the metrics and draw a contact sheet."""
import json

import cv2
import numpy as np

from . import catalog, paths
from .analyze import load_reduced

PINS_FILE = paths.REVIEW_DIR / "pilot_pins.json"   # optional: ["20190519_112346.jpg", ...]


def _load(con) -> list[dict]:
    rows = con.execute(
        "SELECT p.*, m.data FROM photos p JOIN metrics m ON m.photo_id=p.id "
        "WHERE p.role='original' ORDER BY p.capture_order"
    ).fetchall()
    return [{**dict(r), **json.loads(r["data"])} for r in rows]


def select(con) -> list[dict]:
    photos = _load(con)
    chosen: dict[str, dict] = {}

    def pick(reason: str, pool, key, count: int = 1) -> None:
        added = 0
        for p in sorted(pool, key=key):
            if p["id"] not in chosen:
                chosen[p["id"]] = {"reason": reason, **p}
                added += 1
                if added == count:
                    return

    if PINS_FILE.exists():
        pins = set(json.loads(PINS_FILE.read_text(encoding="utf-8")))
        pick("pinned by you", [p for p in photos if p["filename"] in pins], lambda p: p["capture_order"], len(pins))

    found = [p for p in photos if p["content_found"]]
    tidy = [p for p in found if p["paper_fraction"] > 0.15 and not p["content_touches_border"]]
    ease = lambda p: p["content_rectangularity"] - p["border_nonpaper_fraction"] - 0.3 * p["content_touches_border"]

    pick("easy crop", [p for p in tidy if abs(p["content_skew_deg"]) < 2], lambda p: -p["content_rectangularity"])
    pick("hard crop", photos, ease)
    pick("most skewed", [p for p in tidy if p["content_rectangularity"] > 0.85], lambda p: -abs(p["content_skew_deg"]))
    pick("portrait print (rotation test)", [p for p in tidy if p["content_shape"] == "portrait"],
         lambda p: -p["content_rectangularity"])
    pick("most faded", [p for p in found if p["content_fraction"] > 0.2], lambda p: p["contrast"])
    pick("already clean", tidy, lambda p: -(p["contrast"] + 0.2 * p["sharpness"] ** 0.5))
    pick("white / glare inside", found, lambda p: -p["white_inside_fraction"])
    pick("softest", [p for p in found if p["content_fraction"] > 0.2], lambda p: p["sharpness"])
    pick("pale subject against paper", [p for p in found if p["content_fraction"] > 0.1],
         lambda p: p["chroma_mean"])

    segments = sorted({p["segment"] for p in photos})
    for seg, label in ((segments[0], "early capture (event TBD)"), (segments[-1], "late capture (event TBD)")):
        members = [p for p in tidy if p["segment"] == seg] or [p for p in photos if p["segment"] == seg]
        mid = members[len(members) // 2]["capture_order"]
        pick(label, members, lambda p: abs(p["capture_order"] - mid))

    variants = {r["variant_group"]: json.loads(r["data"]) for r in con.execute("SELECT * FROM variants")}
    if variants:
        group = min(variants, key=lambda g: variants[g]["gray_correlation"])
        pick(f"variant pair, biggest edit ({variants[group]['kind']})",
             [p for p in photos if p["variant_group"] == group], lambda p: 0)
        edited = con.execute("SELECT * FROM photos WHERE variant_group=? AND role='edited'", (group,)).fetchone()
        if edited:
            chosen[edited["id"]] = {"reason": "variant pair, edited version", **dict(edited)}

    return sorted(chosen.values(), key=lambda p: (p["capture_order"], p["role"] != "original"))


def contact_sheet(items: list[dict], out_path, columns: int = 4, tile_w: int = 520) -> None:
    tiles = []
    for p in items:
        img = load_reduced(paths.SOURCE_DIR / p["filename"], 4)
        scale = tile_w / max(img.shape[:2])
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        tile = np.full((tile_w + 56, tile_w, 3), 32, np.uint8)
        y, x = (tile_w - img.shape[0]) // 2, (tile_w - img.shape[1]) // 2
        tile[y:y + img.shape[0], x:x + img.shape[1]] = img
        for line, text in enumerate((f"#{p['capture_order']}  {p['stem']}", p["reason"])):
            cv2.putText(tile, text[:52], (8, tile_w + 22 + 24 * line), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (235, 235, 235), 1, cv2.LINE_AA)
        tiles.append(tile)
    while len(tiles) % columns:
        tiles.append(np.full_like(tiles[0], 32))
    sheet = np.vstack([np.hstack(tiles[i:i + columns]) for i in range(0, len(tiles), columns)])
    out_path = paths.assert_not_in_source(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    buf.tofile(out_path)


def run() -> None:
    con = catalog.connect()
    items = select(con)
    con.close()
    keep = ("id", "filename", "role", "capture_order", "segment", "reason", "flags", "content_rectangularity",
            "content_skew_deg", "content_shape", "contrast", "sharpness", "paper_fraction", "white_inside_fraction")
    catalog.write_text_atomic(
        paths.REPORTS_DIR / "pilot_selection.json",
        json.dumps([{k: p.get(k) for k in keep} for p in items], indent=2, ensure_ascii=False),
    )
    contact_sheet(items, paths.PREVIEWS_DIR / "pilot" / "contact_sheet.jpg")
    print(f"pilot-select: {len(items)} files")
    for p in items:
        print(f"  #{p['capture_order']:>3} {p['filename']:<32} {p['reason']}")
