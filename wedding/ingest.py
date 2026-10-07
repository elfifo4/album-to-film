"""Stage 1: inventory the source folder into the catalog. Reads only."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime

from PIL import Image

from . import catalog, paths

NAME_RE = re.compile(r"^(?P<base>\d{8}_\d{6})(?P<suffix>-edited|-COLLAGE)?\.jpe?g$", re.IGNORECASE)
SIDECAR_SUFFIX = ".supplemental-metadata.json"
EXIF_IFD = 0x8769
EXIF_KEEP = {271: "Make", 272: "Model", 274: "Orientation", 306: "DateTime", 36867: "DateTimeOriginal"}


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_image_info(path) -> dict:
    with Image.open(path) as im:
        width, height = im.size
        exif = im.getexif()
        tags = {**dict(exif), **dict(exif.get_ifd(EXIF_IFD))}
    kept = {name: str(tags[tag]) for tag, name in EXIF_KEEP.items() if tag in tags}
    return {
        "width": width,
        "height": height,
        "exif_orientation": int(tags[274]) if 274 in tags else None,
        "exif_datetime": kept.get("DateTimeOriginal") or kept.get("DateTime"),
        "exif_json": json.dumps(kept, ensure_ascii=False),
    }


def read_sidecar(original_filename: str) -> dict:
    """Edited variants share the sidecar of their original."""
    sidecar = paths.SOURCE_DIR / (original_filename + SIDECAR_SUFFIX)
    if not sidecar.exists():
        return {"sidecar": None, "taken_ts": None, "google_url": None}
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    ts = data.get("photoTakenTime", {}).get("timestamp")
    return {"sidecar": sidecar.name, "taken_ts": int(ts) if ts else None, "google_url": data.get("url")}


def classify_name(filename: str) -> dict:
    m = NAME_RE.match(filename)
    if not m:
        stem = filename.rsplit(".", 1)[0]
        return {"role": "original", "variant_group": stem, "filename_time": None}
    base, suffix = m.group("base"), (m.group("suffix") or "").lower()
    role = {"": "original", "-edited": "edited", "-collage": "collage"}[suffix]
    group = base if role != "collage" else base + "-COLLAGE"
    when = datetime.strptime(base, "%Y%m%d_%H%M%S").isoformat()
    return {"role": role, "variant_group": group, "filename_time": when}


def assign_order_and_segments(con, gap_minutes: float) -> None:
    """Capture order is per variant group; a new segment starts after a long pause between captures."""
    groups = con.execute(
        "SELECT variant_group, MIN(filename_time) AS t FROM photos "
        "WHERE role != 'collage' AND filename_time IS NOT NULL GROUP BY variant_group ORDER BY t"
    ).fetchall()
    segment, previous = 0, None
    for order, g in enumerate(groups, start=1):
        t = datetime.fromisoformat(g["t"])
        if previous is None or (t - previous).total_seconds() > gap_minutes * 60:
            segment += 1
        previous = t
        con.execute(
            "UPDATE photos SET capture_order=?, segment=? WHERE variant_group=?",
            (order, segment, g["variant_group"]),
        )


def write_reports(con) -> dict:
    rows = con.execute("SELECT * FROM photos ORDER BY capture_order, role DESC, filename").fetchall()
    buf = io.StringIO()
    cols = ["id", "filename", "role", "variant_group", "capture_order", "segment", "filename_time",
            "width", "height", "bytes", "exif_orientation", "exif_datetime", "sidecar", "sha256"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r[c] for c in cols])
    catalog.write_text_atomic(paths.CATALOG_DIR / "manifest.csv", buf.getvalue())

    segs = con.execute(
        "SELECT segment, COUNT(DISTINCT variant_group) AS photos, MIN(capture_order) AS first_order, "
        "MAX(capture_order) AS last_order, MIN(filename_time) AS start, MAX(filename_time) AS end "
        "FROM photos WHERE role='original' GROUP BY segment ORDER BY segment"
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["segment", "photos", "first_order", "last_order", "start", "end"])
    for s in segs:
        w.writerow(list(s))
    catalog.write_text_atomic(paths.REPORTS_DIR / "capture_segments.csv", buf.getvalue())

    # Template for cheap human range labelling; never overwritten once it exists.
    ranges = paths.EDITORIAL_DIR / "event_ranges.json"
    if not ranges.exists():
        template = {
            "_help": "Label ranges of capture_order as henna | invitation | wedding | uncertain. "
                     "Split or merge ranges freely; see previews/browse/index.html for the numbers.",
            "ranges": [{"first_order": s["first_order"], "last_order": s["last_order"],
                        "category": None, "note": f"capture segment {s['segment']}"} for s in segs],
        }
        catalog.write_text_atomic(ranges, json.dumps(template, indent=2, ensure_ascii=False))

    roles = dict(con.execute("SELECT role, COUNT(*) FROM photos GROUP BY role").fetchall())
    exact = con.execute(
        "SELECT COUNT(*) FROM (SELECT sha256 FROM photos GROUP BY sha256 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    summary = {
        "files": len(rows),
        "roles": roles,
        "variant_groups_with_edit": con.execute(
            "SELECT COUNT(DISTINCT variant_group) FROM photos WHERE role='edited'").fetchone()[0],
        "exact_duplicate_sets": exact,
        "with_exif_orientation": con.execute(
            "SELECT COUNT(*) FROM photos WHERE exif_orientation IS NOT NULL AND exif_orientation != 1").fetchone()[0],
        "without_sidecar": con.execute("SELECT COUNT(*) FROM photos WHERE sidecar IS NULL").fetchone()[0],
        "dimensions": {f"{r[0]}x{r[1]}": r[2] for r in con.execute(
            "SELECT width, height, COUNT(*) FROM photos GROUP BY width, height ORDER BY 3 DESC")},
        "segments": [dict(s) for s in segs],
    }
    catalog.write_text_atomic(paths.REPORTS_DIR / "ingest_summary.json",
                              json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def run() -> dict:
    cfg = catalog.load_config("thresholds.json")
    con = catalog.connect()
    files = sorted(p for p in paths.SOURCE_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
    added = skipped = 0
    for p in files:
        st = p.stat()
        known = con.execute("SELECT bytes, fs_mtime FROM photos WHERE filename=?", (p.name,)).fetchone()
        if known and known["bytes"] == st.st_size and known["fs_mtime"] == st.st_mtime:
            skipped += 1
            continue
        digest = sha256_file(p)
        name = classify_name(p.name)
        original_name = name["variant_group"] + p.suffix if name["role"] == "edited" else p.name
        row = {
            "id": f"{digest[:8]}_{p.stem}", "filename": p.name, "stem": p.stem, "sha256": digest,
            "bytes": st.st_size, "fs_mtime": st.st_mtime, "capture_order": None, "segment": None,
            **name, **read_image_info(p), **read_sidecar(original_name),
        }
        con.execute("DELETE FROM photos WHERE filename=?", (p.name,))
        con.execute(
            f"INSERT INTO photos ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values())
        )
        added += 1
    assign_order_and_segments(con, cfg["ingest"]["segment_gap_minutes"])
    con.commit()
    summary = write_reports(con)
    con.close()
    print(f"ingest: {added} added/updated, {skipped} unchanged, {summary['files']} in catalog")
    print(f"  roles: {summary['roles']}  exact-duplicate sets: {summary['exact_duplicate_sets']}")
    for s in summary["segments"]:
        print(f"  segment {s['segment']}: {s['photos']} photos, order {s['first_order']}-{s['last_order']}, "
              f"{s['start'][11:]} to {s['end'][11:]}")
    return summary


def verify() -> bool:
    """Re-hash every source file and compare with the catalog. Any difference is reported."""
    con = catalog.connect()
    known = {r["filename"]: r["sha256"] for r in con.execute("SELECT filename, sha256 FROM photos")}
    con.close()
    present = {p.name for p in paths.SOURCE_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg")}
    changed = [n for n in sorted(present & known.keys()) if sha256_file(paths.SOURCE_DIR / n) != known[n]]
    missing, new = sorted(known.keys() - present), sorted(present - known.keys())
    ok = not (changed or missing or new)
    print(f"verify: {len(known)} catalogued, changed={len(changed)} missing={len(missing)} new={len(new)}"
          f" -> {'OK, source untouched' if ok else 'MISMATCH'}")
    for label, items in (("changed", changed), ("missing", missing), ("new", new)):
        for n in items[:20]:
            print(f"  {label}: {n}")
    return ok
