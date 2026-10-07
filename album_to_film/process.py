"""Restore stage: detect, orient and enhance each photograph, and record everything for review.

Runs on the pilot set or on every original capture.

Manual decisions in review/overrides.json (corners, rotation, enhancement level) always win over
the automatic result and are never overwritten by the pipeline.
"""
import json
import shutil
import time

import cv2
import numpy as np

from . import catalog, detect, orient, paths, restore
from .analyze import load_reduced

STAGE_VERSION = 7
PREVIEW_LONG_SIDE = 1100
CAPTURE_LONG_SIDE = 2656     # sharp enough for the magnifier when setting corners by hand
LEVEL_COLOURS = {"high": (90, 200, 90), "medium": (60, 200, 235), "low": (70, 70, 230), "manual": (235, 160, 60)}
OVERRIDES_FILE = paths.REVIEW_DIR / "overrides.json"


def save_jpg(path, image: np.ndarray, quality: int, long_side: int | None = None) -> None:
    path = paths.assert_not_in_source(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if long_side and max(image.shape[:2]) > long_side:
        scale = long_side / max(image.shape[:2])
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tofile(path)


def load_overrides() -> dict:
    return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8")) if OVERRIDES_FILE.exists() else {}


def save_overrides(overrides: dict) -> None:
    catalog.write_text_atomic(OVERRIDES_FILE, json.dumps(overrides, indent=2, ensure_ascii=False))


def load_event_ranges() -> dict:
    f = paths.EDITORIAL_DIR / "event_ranges.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"ranges": [], "exceptions": []}


def category_for(order: int, events: dict) -> str:
    for e in events.get("exceptions", []):
        if e.get("capture_order") == order:
            return e["category"]
    for r in events.get("ranges", []):
        if r["first_order"] <= order <= r["last_order"] and r.get("category"):
            return r["category"]
    return "uncertain"


def process_file(row: dict, variant: dict | None, cfg: dict, ecfg: dict, override: dict | None = None) -> dict:
    override = override or {}
    full = load_reduced(paths.SOURCE_DIR / row["filename"], 1)   # as displayed (EXIF applied)
    H, W = full.shape[:2]
    s = cfg["detect"]["reduce"]
    small = cv2.resize(full, (W // s, H // s), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    sx, sy = W / sw, H / sh

    det = detect.detect_print(small, cfg["detect"])
    automatic = {"status": det["status"], "confidence": det["confidence"], "level": det["level"]}
    if override.get("quad"):      # corners you clicked, as fractions of the capture
        manual = np.array(override["quad"], np.float32) * [sw - 1, sh - 1]
        det.update(status="manual", quad=detect._order_corners(manual), confidence=1.0, level="manual", parts={}, flags=[])
    flags = list(det["flags"])
    corrected = restore.apply_gain(full, restore.capture_correction(small, det["model"], ecfg))

    overlay = small.copy()
    cv2.polylines(overlay, [det["quad"].astype(np.int32)], True, LEVEL_COLOURS[det["level"]], 3, cv2.LINE_AA)

    trims = {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
    cropped = det["status"] in ("found", "manual") and det["level"] != "low"
    if cropped:
        margin = cfg["crop"]["outward_margin_fraction"] if det["status"] == "found" else 0.0
        quad_full = restore.expand_quad(det["quad"] * [sx, sy], margin, W, H)
        size = restore.target_size(quad_full)
        geom_raw, geom = restore.warp(full, quad_full, size), restore.warp(corrected, quad_full, size)
        if det["status"] == "found":
            paper_small = det["model"]["paper"].astype(np.uint8) * 255
            quad_small = restore.expand_quad(det["quad"], margin, sw, sh)
            paper_warped = restore.warp(paper_small, quad_small, (max(size[0] // s, 8), max(size[1] // s, 8)), cv2.INTER_NEAREST)
            trims = restore.evidence_trim(paper_warped, margin, cfg["crop"])
            if any(trims.values()):
                flags.append("inward_trim_on_paper_evidence")
            trims = {side: round(v + cfg["crop"]["edge_inset_fraction"], 4) for side, v in trims.items()}
            geom_raw, geom = restore.apply_trim(geom_raw, trims), restore.apply_trim(geom, trims)
    else:
        geom_raw, geom = full, corrected
        if det["status"] == "found":
            flags.append("crop_not_applied_low_confidence")

    if row["role"] == "edited":
        edit_rotation = 0           # you already oriented this file
    else:
        edit_rotation = variant["rotation_cw_deg"] if variant else None
    vote = orient.faces_vote(geom, cfg["orient"])
    if "rotation_cw_deg" in override:
        turn = {"rotation_cw_deg": int(override["rotation_cw_deg"]) % 360, "source": "manual", "confidence": "high", "flags": []}
    else:
        turn = orient.decide(edit_rotation, vote)
    flags += turn["flags"]
    k = turn["rotation_cw_deg"] // 90
    rot = lambda img: np.ascontiguousarray(np.rot90(img, -k))

    light, light_info = restore.enhance(geom, "light", ecfg)
    standard, standard_info = restore.enhance(geom, "standard", ecfg)
    versions = {"none": geom, "light": light, "standard": standard}
    chosen = override.get("level", ecfg["default_level"])
    if "level" not in override and chosen != "none" and {"light": light_info, "standard": standard_info}[chosen]["may_hurt"]:
        flags.append("enhancement_may_hurt")
        chosen = "none"

    pid = row["id"]
    processed = paths.PROCESSED_DIR / f"{pid}.jpg"
    save_jpg(processed, rot(versions[chosen]), 95)
    previews = paths.PREVIEWS_DIR / "photos"
    save_jpg(previews / f"{pid}_capture.jpg", full, 85, CAPTURE_LONG_SIDE)
    save_jpg(previews / f"{pid}_overlay.jpg", overlay, 85)
    save_jpg(previews / f"{pid}_geom.jpg", rot(geom_raw), 88, PREVIEW_LONG_SIDE)
    save_jpg(previews / f"{pid}_light.jpg", rot(light), 88, PREVIEW_LONG_SIDE)
    save_jpg(previews / f"{pid}_standard.jpg", rot(standard), 88, PREVIEW_LONG_SIDE)

    out_h, out_w = rot(geom).shape[:2]
    return {
        "id": pid, "filename": row["filename"], "role": row["role"], "capture_order": row["capture_order"],
        "variant_kind": variant["kind"] if variant else None,
        "detect": {"status": det["status"], "confidence": det["confidence"], "level": det["level"],
                   "parts": det["parts"], "quad_full": (det["quad"] * [sx, sy]).round(1).tolist(),
                   "quad_fraction": (det["quad"] / [sw - 1, sh - 1]).round(4).tolist(),
                   "cropped": cropped, "trims": trims, "automatic": automatic},
        "orientation": {**{k_: v for k_, v in turn.items() if k_ != "flags"}, "faces": vote},
        "enhancement": {"chosen": chosen, "light": light_info, "standard": standard_info},
        "output": {"width": out_w, "height": out_h, "processed": str(processed.relative_to(paths.ROOT))},
        "flags": flags, "override": override,
    }


def write_editorial(results: list[dict], metrics: dict, events: dict, filename: str) -> None:
    """The shared per-photo editorial records that both films will select from."""
    records = []
    for r in results:
        category = category_for(r["capture_order"], events)
        o = r["override"]
        records.append({
            "id": r["id"], "source_path": f"{paths.SOURCE_DIR.name}/{r['filename']}",
            "processed_path": r["output"]["processed"], "source_variant": r["role"],
            "category": category, "category_confidence": "human range label" if category != "uncertain" else None,
            "chapter": category, "capture_order": r["capture_order"],
            "crop_confidence": r["detect"]["confidence"], "orientation_confidence": r["orientation"]["confidence"],
            "sharpness": metrics.get(r["id"], {}).get("sharpness"),
            "quality_score": None, "emotional_score": None, "uniqueness_score": None, "duplicate_group": None,
            "use_in_trailer": None, "use_in_full_movie": None, "excluded": bool(o.get("exclude")),
            "preferred_duration": None, "min_duration": None, "max_duration": None, "duration_weight": 1.0,
            "motion_style": None, "transition_in": None, "transition_out": None, "crop_mode": None,
            "flags": r["flags"], "notes": o.get("note", ""), "manual_override": o,
        })
    catalog.write_text_atomic(paths.EDITORIAL_DIR / filename, json.dumps(records, indent=2, ensure_ascii=False))


def process_one(con, photo_id: str, cfg: dict, ecfg: dict, overrides: dict, events: dict, quiet: bool = False) -> dict:
    """Process one file unless an identical result (same source, code, config, override) is cached."""
    row = dict(con.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone())
    v = con.execute("SELECT data FROM variants WHERE variant_group=?", (row["variant_group"],)).fetchone()
    variant = json.loads(v["data"]) if v else None
    if variant and variant["rotation_cw_deg"] is None and row["role"] != "edited":
        variant = None      # the edit is a crop: it says nothing about this file's rotation
    override = overrides.get(photo_id, {})
    geometry = {k: override[k] for k in ("quad", "rotation_cw_deg", "level") if k in override}
    fp = catalog.fingerprint(row["sha256"], STAGE_VERSION, cfg["detect"], cfg["crop"], cfg["orient"], ecfg, variant, geometry)
    cached = con.execute("SELECT data FROM restore_results WHERE photo_id=?", (photo_id,)).fetchone()
    if cached and catalog.is_done(con, photo_id, "restore", fp):
        result = json.loads(cached["data"])
        result["override"] = override
        return result
    t0 = time.time()
    result = process_file(row, variant, cfg, ecfg, override)
    result["seconds"] = round(time.time() - t0, 1)
    result["category"] = category_for(row["capture_order"], events)
    con.execute("INSERT OR REPLACE INTO restore_results VALUES (?,?)", (photo_id, json.dumps(result)))
    catalog.mark(con, photo_id, "restore", fp)
    con.commit()
    if not quiet:
        d, o = result["detect"], result["orientation"]
        print(f"  #{row['capture_order']:>3} {row['filename']:<30} crop {d['level']:<6} {d['confidence']:.2f}  "
              f"turn {o['rotation_cw_deg']:>3} ({o['source']})  {result['seconds']}s  {' '.join(result['flags'])}")
    return result


REVIEW_FLAGS = {"no_print_found", "crop_not_applied_low_confidence", "uncertain_orientation",
                "faces_disagree_with_your_edit", "multiple_regions", "enhancement_may_hurt", "no_paper_visible"}


def needs_review(result: dict) -> bool:
    """True while the automatic result is doubtful and you have not settled it."""
    o = result.get("override", {})
    if o.get("exclude") or o.get("reviewed"):
        return False
    return (result["detect"]["level"] in ("low", "medium") or result["orientation"]["confidence"] == "low"
            or bool(REVIEW_FLAGS & set(result["flags"])))


def selection_ids(con, scope: str) -> list[str]:
    if scope == "pilot":
        return [i["id"] for i in json.loads((paths.REPORTS_DIR / "pilot_selection.json").read_text(encoding="utf-8"))]
    return [r["id"] for r in con.execute("SELECT id FROM photos WHERE role='original' ORDER BY capture_order")]


def run(scope: str = "all", quiet: bool = False) -> list[dict]:
    """Process every photo in scope ('pilot' or 'all'); unchanged photos are served from the cache."""
    cfg, ecfg = catalog.load_config("thresholds.json"), catalog.load_config("enhancement.json")
    events, overrides = load_event_ranges(), load_overrides()
    con = catalog.connect()
    con.execute("CREATE TABLE IF NOT EXISTS restore_results (photo_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    ids = selection_ids(con, scope)
    free_gb = shutil.disk_usage(paths.ROOT).free / 1e9
    if free_gb < cfg["restore"]["min_free_disk_gb"]:
        raise SystemExit(f"Only {free_gb:.1f} GB free; refusing to start (minimum {cfg['restore']['min_free_disk_gb']} GB).")

    results, errors, started = [], [], time.time()
    for n, photo_id in enumerate(ids, 1):
        try:
            result = process_one(con, photo_id, cfg, ecfg, overrides, events, quiet=True)
        except Exception as e:      # keep the batch going; the failure is listed at the end
            errors.append((photo_id, repr(e)))
            continue
        result["category"] = category_for(result["capture_order"], events)
        result["needs_review"] = needs_review(result)
        results.append(result)
        if not quiet and n % 25 == 0:
            print(f"  restore {n}/{len(ids)}  ({time.time() - started:.0f}s)")
    metrics = {r["photo_id"]: json.loads(r["data"]) for r in con.execute("SELECT * FROM metrics")}
    con.close()

    suffix = "" if scope == "all" else ".pilot"
    catalog.write_text_atomic(paths.REPORTS_DIR / f"restore_results{suffix}.json", json.dumps(results, indent=2, ensure_ascii=False))
    write_editorial(results, metrics, events, f"photos{suffix}.json")
    if not quiet:
        count = lambda key: dict(sorted(__import__("collections").Counter(key(r) for r in results).items()))
        flags: dict[str, int] = {}
        for r in results:
            for f in r["flags"]:
                flags[f] = flags.get(f, 0) + 1
        print(f"restore ({scope}): {len(results)} photos, {len(errors)} errors, {time.time() - started:.0f}s")
        print("  crop:", count(lambda r: r["detect"]["level"]))
        print("  orientation from:", count(lambda r: r["orientation"]["source"]))
        print("  chapters:", count(lambda r: r["category"]))
        print("  flags:", dict(sorted(flags.items(), key=lambda kv: -kv[1])))
        print("  need review:", sum(r["needs_review"] for r in results))
        for photo_id, err in errors:
            print(f"  ERROR {photo_id}: {err}")
    return results
