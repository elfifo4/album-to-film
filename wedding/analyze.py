"""Stage 2: cheap per-photo measurements on a reduced copy, and original-vs-edited comparison.

Nothing here changes or restores an image; it only measures, to drive flags and pilot selection.
"""
import csv
import io
import json

import cv2
import numpy as np

from . import catalog, paths

STAGE_VERSION = 2
REDUCE_FLAGS = {1: cv2.IMREAD_COLOR, 2: cv2.IMREAD_REDUCED_COLOR_2,
                4: cv2.IMREAD_REDUCED_COLOR_4, 8: cv2.IMREAD_REDUCED_COLOR_8}


def load_reduced(path, reduce: int = 4) -> np.ndarray:
    """Decode at 1/reduce size. Reads bytes via NumPy so non-ASCII paths are safe."""
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), REDUCE_FLAGS[reduce])
    if img is None:
        raise ValueError(f"cannot decode {path}")
    return img


def _r(x, n=3):
    return round(float(x), n)


def measure(bgr: np.ndarray, cfg: dict) -> dict:
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, a, b = lab[..., 0], lab[..., 1] - 128, lab[..., 2] - 128
    chroma = np.hypot(a, b)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Paper = bright and nearly colourless, relative to the brightest part of this frame.
    bright_ref = np.percentile(L, 90)
    floor = max(cfg["paper_min_lightness"], bright_ref - cfg["paper_lightness_tolerance"])
    paper = ((L > floor) & (chroma < cfg["paper_max_chroma"])).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg["morph_kernel_px"],) * 2)
    paper = cv2.morphologyEx(paper, cv2.MORPH_OPEN, k)
    non_paper = cv2.morphologyEx(255 - paper, cv2.MORPH_CLOSE, k)
    non_paper = cv2.morphologyEx(non_paper, cv2.MORPH_OPEN, k)

    ring = np.zeros((h, w), bool)
    t = max(2, int(0.02 * min(h, w)))
    ring[:t], ring[-t:], ring[:, :t], ring[:, -t:] = True, True, True, True
    m = {"border_nonpaper_fraction": _r((non_paper[ring] > 0).mean())}

    contours, _ = cv2.findContours(non_paper, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    content = np.zeros((h, w), np.uint8)
    if contours:
        c = max(contours, key=cv2.contourArea)
        cv2.drawContours(content, [c], -1, 255, cv2.FILLED)
        (_, _), (rw, rh), angle = cv2.minAreaRect(c)
        skew = angle % 90
        skew = skew - 90 if skew > 45 else skew
        x, y, bw, bh = cv2.boundingRect(c)
        m.update({
            "content_found": True,
            "content_fraction": _r(cv2.contourArea(c) / (h * w)),
            "content_rectangularity": _r(cv2.contourArea(c) / max(rw * rh, 1)),
            "content_skew_deg": _r(skew, 2),
            "content_aspect": _r(max(rw, rh) / max(min(rw, rh), 1)),
            "content_shape": "landscape" if bw >= bh else "portrait",
            "content_bbox": [_r(x / w), _r(y / h), _r(bw / w), _r(bh / h)],
            "content_touches_border": bool(x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1),
            "other_regions": sum(1 for o in contours if cv2.contourArea(o) > 0.02 * h * w) - 1,
        })
    else:
        content[:] = 255
        m.update({"content_found": False, "content_fraction": 0.0, "content_rectangularity": 0.0,
                  "content_skew_deg": 0.0, "content_aspect": 0.0, "content_shape": "unknown",
                  "content_bbox": [0, 0, 1, 1], "content_touches_border": True, "other_regions": 0})

    inside = content > 0
    Lc = L[inside]
    p1, p50, p99 = np.percentile(Lc, [1, 50, 99])
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    m.update({
        "L_p1": _r(p1, 1), "L_p50": _r(p50, 1), "L_p99": _r(p99, 1),
        "contrast": _r(p99 - p1, 1),
        "chroma_mean": _r(chroma[inside].mean(), 2),
        "cast_a": _r(a[inside].mean(), 2), "cast_b": _r(b[inside].mean(), 2),
        "highlight_clip": _r((Lc >= 250).mean(), 4),
        "shadow_clip": _r((Lc <= 8).mean(), 4),
        "sharpness": _r(lap[inside].var(), 1),
        "white_inside_fraction": _r((paper[inside] > 0).mean()),
    })

    paper_only = (paper > 0) & ~inside
    m["paper_fraction"] = _r(paper_only.mean())
    if paper_only.sum() > 500:
        smooth = cv2.GaussianBlur(L, (0, 0), 15)[paper_only]
        lo, hi = np.percentile(smooth, [5, 95])
        m.update({"paper_L": _r(L[paper_only].mean(), 1), "paper_unevenness": _r(hi - lo, 1),
                  "paper_cast_a": _r(a[paper_only].mean(), 2), "paper_cast_b": _r(b[paper_only].mean(), 2)})
    else:
        m.update({"paper_L": None, "paper_unevenness": None, "paper_cast_a": None, "paper_cast_b": None})
    return m


def flags_for(m: dict, f: dict) -> list[str]:
    out = []
    if not m["content_found"]:
        out.append("no_content_found")
    if m["paper_fraction"] < f["no_paper_fraction"]:
        out.append("no_paper")
    if m["border_nonpaper_fraction"] > f["table_visible_border_fraction"]:
        out.append("table_or_edge_visible")
    if m["other_regions"] > 0:
        out.append("multiple_regions")
    if m["contrast"] < f["low_contrast"]:
        out.append("low_contrast")
    if m["highlight_clip"] > f["highlight_clip_fraction"]:
        out.append("possible_overexposure")
    if m["sharpness"] < f["severe_blur_sharpness"]:
        out.append("possible_blur")
    if m["white_inside_fraction"] > f["white_inside_fraction"]:
        out.append("white_or_glare_inside")
    return out


def compare_variants(orig: np.ndarray, edit: np.ndarray, cfg: dict) -> dict:
    """What did the Google Photos edit change relative to the original as displayed (EXIF applied)?

    Tries the four right-angle rotations of the original; the best match tells whether the edit is a
    rotation, a tonal change, both, or something else (crop).
    """
    gray = lambda x: cv2.cvtColor(x, cv2.COLOR_BGR2GRAY).astype(np.float32)
    ge = gray(edit)
    best_k, corr, aligned = None, -1.0, None
    for k in range(4):
        cand = np.rot90(orig, -k)  # k quarter turns clockwise
        if cand.shape != edit.shape:
            continue
        c = float(np.corrcoef(gray(cand).ravel(), ge.ravel())[0, 1])
        if c > corr:
            best_k, corr, aligned = k, c, cand
    if aligned is None or corr < cfg["match_min_correlation"]:
        return {"kind": "crop_or_other", "rotation_cw_deg": None, "mean_abs_diff": None,
                "gray_correlation": _r(max(corr, 0), 4), "brightness_delta": None, "contrast_ratio": None,
                "saturation_ratio": None, "sharpness_ratio": None,
                "edit_size": f"{edit.shape[1]}x{edit.shape[0]}"}
    orig, go = np.ascontiguousarray(aligned), gray(aligned)
    so, se = (cv2.cvtColor(x, cv2.COLOR_BGR2HSV)[..., 1].mean() for x in (orig, edit))
    mad = float(np.abs(orig.astype(np.float32) - edit.astype(np.float32)).mean())
    tonal = mad >= cfg["recompress_mean_abs_diff"]
    kind = {(False, False): "same", (True, False): "rotated",
            (False, True): "tonal", (True, True): "rotated+tonal"}[(best_k != 0, tonal)]
    return {
        "kind": kind, "rotation_cw_deg": best_k * 90, "mean_abs_diff": _r(mad, 2), "gray_correlation": _r(corr, 4),
        "brightness_delta": _r(ge.mean() - go.mean(), 1),
        "contrast_ratio": _r(ge.std() / max(go.std(), 1e-6)),
        "saturation_ratio": _r(se / max(so, 1e-6)),
        "sharpness_ratio": _r(cv2.Laplacian(ge, cv2.CV_32F).var() / max(cv2.Laplacian(go, cv2.CV_32F).var(), 1e-6)),
        "edit_size": f"{edit.shape[1]}x{edit.shape[0]}",
    }


def write_reports(con) -> None:
    rows = con.execute(
        "SELECT p.id, p.filename, p.role, p.capture_order, p.segment, m.data FROM photos p "
        "JOIN metrics m ON m.photo_id = p.id ORDER BY p.capture_order, p.role DESC"
    ).fetchall()
    if rows:
        keys = [k for k in json.loads(rows[0]["data"]) if k != "content_bbox"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "filename", "role", "capture_order", "segment", *keys])
        for r in rows:
            d = json.loads(r["data"])
            d["flags"] = " ".join(d["flags"])
            w.writerow([r["id"], r["filename"], r["role"], r["capture_order"], r["segment"],
                        *[d.get(k) for k in keys]])
        catalog.write_text_atomic(paths.REPORTS_DIR / "metrics.csv", buf.getvalue())

    vrows = con.execute("SELECT variant_group, data FROM variants ORDER BY variant_group").fetchall()
    if vrows:
        keys = list(json.loads(vrows[0]["data"]))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["variant_group", *keys])
        for r in vrows:
            d = json.loads(r["data"])
            w.writerow([r["variant_group"], *[d.get(k) for k in keys]])
        catalog.write_text_atomic(paths.REPORTS_DIR / "variants.csv", buf.getvalue())


def run(limit: int | None = None) -> None:
    cfg = catalog.load_config("thresholds.json")
    acfg, reduce = cfg["analyze"], cfg["analyze"]["decode_reduce"]
    con = catalog.connect()
    photos = con.execute("SELECT * FROM photos WHERE role != 'collage' ORDER BY capture_order, role DESC").fetchall()
    if limit:
        photos = photos[:limit]

    done = skipped = errors = 0
    for i, p in enumerate(photos, 1):
        fp = catalog.fingerprint(p["sha256"], STAGE_VERSION, acfg, cfg["flags"])
        if catalog.is_done(con, p["id"], "analyze", fp):
            skipped += 1
            continue
        try:
            m = measure(load_reduced(paths.SOURCE_DIR / p["filename"], reduce), acfg)
            m["flags"] = flags_for(m, cfg["flags"])
            con.execute("INSERT OR REPLACE INTO metrics VALUES (?,?)", (p["id"], json.dumps(m)))
            catalog.mark(con, p["id"], "analyze", fp)
            done += 1
        except Exception as e:  # keep the batch going; the failure is recorded per file
            catalog.mark(con, p["id"], "analyze", fp, "error", repr(e))
            errors += 1
        if i % 50 == 0:
            con.commit()
            print(f"  analyze {i}/{len(photos)}")
    con.commit()

    by_group: dict[str, dict] = {}
    for p in photos:
        by_group.setdefault(p["variant_group"], {})[p["role"]] = p
    compared = 0
    for group, v in by_group.items():
        if "original" not in v or "edited" not in v:
            continue
        fp = catalog.fingerprint(v["original"]["sha256"], v["edited"]["sha256"], STAGE_VERSION, cfg["variants"])
        if catalog.is_done(con, v["edited"]["id"], "variant_compare", fp):
            continue
        result = compare_variants(load_reduced(paths.SOURCE_DIR / v["original"]["filename"], reduce),
                                  load_reduced(paths.SOURCE_DIR / v["edited"]["filename"], reduce),
                                  cfg["variants"])
        con.execute("INSERT OR REPLACE INTO variants VALUES (?,?)", (group, json.dumps(result)))
        catalog.mark(con, v["edited"]["id"], "variant_compare", fp)
        compared += 1
    con.commit()
    write_reports(con)

    print(f"analyze: {done} measured, {skipped} unchanged, {errors} errors; {compared} variant pairs compared")
    counts: dict[str, int] = {}
    for r in con.execute("SELECT m.data FROM metrics m JOIN photos p ON p.id=m.photo_id WHERE p.role='original'"):
        for f in json.loads(r["data"])["flags"]:
            counts[f] = counts.get(f, 0) + 1
    print("  flags on originals:", dict(sorted(counts.items(), key=lambda kv: -kv[1])) or "none")
    kinds: dict[str, int] = {}
    for r in con.execute("SELECT data FROM variants"):
        k = json.loads(r["data"])["kind"]
        kinds[k] = kinds.get(k, 0) + 1
    print("  edited variants by kind:", kinds or "none")
    con.close()
