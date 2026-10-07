"""Stage 3: group repeated captures of the same printed photograph. Flags only; nothing is deleted.

Runs on the restored photographs (cropped and upright), so two captures of one print line up closely.
Similarity is the correlation of small normalised thumbnails; a perceptual hash is reported alongside.
"""
import json

import cv2
import numpy as np

from . import catalog, paths


def _load_gray(path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_4)


def _thumb(gray: np.ndarray, n: int = 64) -> np.ndarray:
    t = cv2.resize(gray, (n, n), interpolation=cv2.INTER_AREA).astype(np.float32)
    return ((t - t.mean()) / (t.std() + 1e-6)).ravel() / n


def _phash(gray: np.ndarray) -> np.ndarray:
    d = cv2.dct(cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32))[:8, :8]
    return (d > np.median(d.ravel()[1:])).ravel()


def _sharpness(gray: np.ndarray) -> float:
    scale = 800 / max(gray.shape)
    g = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def _aligned(gray: np.ndarray) -> np.ndarray:
    scale = 512 / max(gray.shape)
    return cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def same_print_evidence(a: np.ndarray, b: np.ndarray) -> dict | None:
    """Align b onto a by matched features, then compare block by block.

    Two captures of one print agree everywhere once aligned. Two different shots of the same scene
    align on the background but disagree wherever people moved, which pulls the block scores down.
    """
    orb = cv2.ORB_create(2000)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 12:
        return None
    src = np.float32([kb[m.trainIdx].pt for m in matches])
    dst = np.float32([ka[m.queryIdx].pt for m in matches])
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if H is None:
        return None
    size = (a.shape[1], a.shape[0])
    warped = cv2.warpPerspective(b, H, size)
    valid = cv2.warpPerspective(np.full_like(b, 255), H, size) > 0
    fa, fb = (cv2.GaussianBlur(x, (0, 0), 2).astype(np.float32) for x in (a, warped))
    n, scores = 8, []
    bh, bw = a.shape[0] // n, a.shape[1] // n
    for i in range(n):
        for j in range(n):
            block = (slice(i * bh, (i + 1) * bh), slice(j * bw, (j + 1) * bw))
            x, y = fa[block].ravel(), fb[block].ravel()
            if valid[block].mean() < 0.9 or x.std() < 4 or y.std() < 4:
                continue        # outside the overlap, or featureless
            scores.append(np.corrcoef(x, y)[0, 1])
    if len(scores) < 8:
        return None
    return {"inlier_ratio": round(float(mask.sum()) / len(matches), 3),
            "block_correlation": round(float(np.median(scores)), 3)}


def run() -> None:
    cfg = catalog.load_config("thresholds.json")["dedupe"]
    results = json.loads((paths.REPORTS_DIR / "restore_results.json").read_text(encoding="utf-8"))
    grays = [_load_gray(paths.ROOT / r["output"]["processed"]) for r in results]
    thumbs = np.array([_thumb(g) for g in grays])
    hashes = np.array([_phash(g) for g in grays])
    corr = thumbs @ thumbs.T
    hamming = (hashes[:, None, :] != hashes[None, :, :]).sum(-1)

    parent = list(range(len(results)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pairs = []
    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            if corr[i, j] < cfg["candidate_correlation"]:
                continue
            evidence = same_print_evidence(_aligned(grays[i]), _aligned(grays[j])) or {}
            same = (evidence.get("block_correlation", 0) >= cfg["same_print_block_correlation"]
                    and evidence.get("inlier_ratio", 0) >= cfg["same_print_min_inlier_ratio"])
            pairs.append({"a": results[i]["capture_order"], "b": results[j]["capture_order"],
                          "kind": "same_print" if same else "similar_shot",
                          "correlation": round(float(corr[i, j]), 3), "hamming": int(hamming[i, j]), **evidence})
            if same:
                parent[find(i)] = find(j)

    members: dict[int, list[int]] = {}
    for i in range(len(results)):
        members.setdefault(find(i), []).append(i)
    groups, per_photo = [], {}
    for n, idx in enumerate(sorted((m for m in members.values() if len(m) > 1), key=lambda m: results[m[0]]["capture_order"]), 1):
        orders = {results[i]["capture_order"] for i in idx}
        sharp = {i: _sharpness(grays[i]) for i in idx}
        keep = max(idx, key=lambda i: sharp[i])
        groups.append({"group": n, "suggested_keep": results[keep]["capture_order"],
                       "members": [{"capture_order": results[i]["capture_order"], "id": results[i]["id"],
                                    "sharpness": round(sharp[i], 1)} for i in idx]})
        for i in idx:
            per_photo[results[i]["id"]] = {"group": n, "suggested_keep": i == keep,
                                           "others": sorted(orders - {results[i]["capture_order"]})}
    similar = [p for p in pairs if p["kind"] == "similar_shot"]

    catalog.write_text_atomic(paths.REPORTS_DIR / "duplicates.json",
                              json.dumps({"groups": groups, "similar_shots": similar, "pairs": pairs, "by_photo": per_photo}, indent=2))
    print(f"dedupe: {len(groups)} groups of the same print, covering {len(per_photo)} of {len(results)} photographs")
    for g in groups:
        print(f"  group {g['group']}: " + ", ".join(
            f"#{m['capture_order']}{'*' if m['capture_order'] == g['suggested_keep'] else ''}" for m in g["members"])
            + "   (* = sharpest)")
    print(f"  similar but different shots ({len(similar)} pairs): " + ", ".join(f"#{p['a']}/#{p['b']}" for p in similar))
