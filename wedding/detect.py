"""Stage 4: find the printed photograph inside a phone capture.

Works on a reduced copy. The white paper is modelled as a smooth brightness surface (the lighting is
uneven), everything that departs from it is "content", and a four-sided outline is fitted to the largest
content region. The result always carries a confidence; low confidence means "do not crop".
"""
import cv2
import numpy as np


def _basis(x, y):
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=-1)


def fit_paper_surface(L: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Least-squares quadratic brightness surface through the paper pixels."""
    h, w = L.shape
    ys, xs = np.nonzero(mask)
    step = max(1, len(xs) // 20000)
    xs, ys = xs[::step], ys[::step]
    coef, *_ = np.linalg.lstsq(_basis(xs / w, ys / h), L[ys, xs], rcond=None)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return (_basis(xx / w, yy / h) @ coef).astype(np.float32)


def paper_model(small_bgr: np.ndarray, cfg: dict) -> dict:
    """Paper mask, its brightness surface and its mean colour. `surface` is None when no paper is visible."""
    lab = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    low_chroma = chroma < cfg["paper_max_chroma"]
    floor = max(cfg["paper_min_lightness"], np.percentile(L, 90) - cfg["paper_lightness_tolerance"])
    paper = (L > floor) & low_chroma
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    surface = None
    for _ in range(cfg["surface_iterations"]):
        core = cv2.erode(paper.astype(np.uint8), k) > 0
        if core.mean() < cfg["min_paper_fraction"]:
            break
        surface = fit_paper_surface(L, core)
        resid = surface - L
        paper = (resid < cfg["surface_tolerance"]) & (resid > -1.5 * cfg["surface_tolerance"]) & low_chroma
    if surface is None:
        return {"paper": np.zeros(L.shape, bool), "surface": None, "paper_bgr": None, "paper_fraction": 0.0}
    core = cv2.erode(paper.astype(np.uint8), k) > 0
    paper_bgr = small_bgr[core].mean(axis=0) if core.any() else None
    return {"paper": paper, "surface": surface, "paper_bgr": paper_bgr, "paper_fraction": float(paper.mean())}


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Clockwise starting from the top-left corner (image coordinates)."""
    c = pts.mean(axis=0)
    pts = pts[np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))]
    return np.roll(pts, -int(np.argmin(pts.sum(axis=1))), axis=0)


def _densify(poly: np.ndarray, spacing: float = 2.0) -> np.ndarray:
    out = []
    for a, b in zip(poly, np.roll(poly, -1, axis=0)):
        n = max(2, int(np.linalg.norm(b - a) / spacing))
        out.append(a + (b - a) * np.linspace(0, 1, n, endpoint=False)[:, None])
    return np.concatenate(out)


def _segment_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def _intersect(l1, l2):
    (vx1, vy1, x1, y1), (vx2, vy2, x2, y2) = l1, l2
    det = vx1 * (-vy2) - (-vx2) * vy1
    if abs(det) < 1e-6:
        return None
    t = ((x2 - x1) * (-vy2) - (-vx2) * (y2 - y1)) / det
    return np.array([x1 + t * vx1, y1 + t * vy1], np.float32)


def fit_quad(contour: np.ndarray) -> tuple[np.ndarray, float]:
    """Four straight sides fitted to the print's outline. Returns corners and mean fit error in pixels.

    Starts from the minimum-area rectangle, then fits each side independently (robust line fit), so a
    perspective-distorted print gives a true quadrilateral rather than a rectangle. Only outline points
    that lie on the convex hull are used: where something pale inside the print (a white dress) meets
    its edge the outline dips inward, and those points say nothing about where the edge is.
    """
    hull = cv2.convexHull(contour)
    box = _order_corners(cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32))
    diag = float(np.linalg.norm(box[2] - box[0]))
    pts = contour.reshape(-1, 2).astype(np.float32)
    on_hull = np.array([abs(cv2.pointPolygonTest(hull, (float(x), float(y)), True)) < 1.5 for x, y in pts])
    pts = pts[on_hull]
    dist = np.stack([_segment_distance(pts, box[i], box[(i + 1) % 4]) for i in range(4)])
    side, nearest = dist.argmin(axis=0), dist.min(axis=0)
    lines, errors = [], []
    for i in range(4):
        p = pts[(side == i) & (nearest < 0.02 * diag)]
        if len(p) < 15:      # side hidden or off-frame: keep the rectangle's side
            v = box[(i + 1) % 4] - box[i]
            v = v / max(np.linalg.norm(v), 1e-9)
            lines.append((v[0], v[1], box[i][0], box[i][1]))
            continue
        vx, vy, x0, y0 = cv2.fitLine(p, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((vx, vy, x0, y0))
        errors.append(np.abs((p[:, 0] - x0) * vy - (p[:, 1] - y0) * vx).mean())
    corners = [_intersect(lines[i - 1], lines[i]) for i in range(4)]
    if any(c is None for c in corners) or max(np.linalg.norm(c - b) for c, b in zip(corners, box)) > 0.08 * diag:
        return box, float(nearest.mean())
    return np.array(corners, np.float32), float(np.mean(errors)) if errors else float(nearest.mean())


def edge_support(quad: np.ndarray, paper: np.ndarray) -> tuple[float, float, float]:
    """Walk the outline: is there paper just outside it and print just inside it?

    Returns (share of the outline with paper outside, share with paper outside and print inside,
    share that lies on the frame edge and cannot be checked).
    """
    h, w = paper.shape
    centre = quad.mean(axis=0)
    short = min(np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[3] - quad[0]))
    outside, supported, on_frame, total = 0, 0, 0, 0
    for a, b in zip(quad, np.roll(quad, -1, axis=0)):
        n = np.array([b[1] - a[1], a[0] - b[0]], np.float32)
        n /= max(np.linalg.norm(n), 1e-9)
        if n @ (centre - a) > 0:
            n = -n                                     # make the normal point away from the print
        steps = max(2, int(np.linalg.norm(b - a) / 2))
        p = a + (b - a) * np.linspace(0.04, 0.96, steps)[:, None]
        out_pt, in_pt = np.rint(p + n * 0.012 * short).astype(int), np.rint(p - n * 0.03 * short).astype(int)
        visible = (out_pt[:, 0] >= 0) & (out_pt[:, 0] < w) & (out_pt[:, 1] >= 0) & (out_pt[:, 1] < h)
        in_pt = np.clip(in_pt, [0, 0], [w - 1, h - 1])
        out_paper = paper[out_pt[visible, 1], out_pt[visible, 0]]
        in_print = ~paper[in_pt[visible, 1], in_pt[visible, 0]]
        total += steps
        on_frame += int((~visible).sum())
        outside += int(out_paper.sum())
        supported += int((out_paper & in_print).sum())
    checked = max(total - on_frame, 1)
    return outside / checked, supported / checked, on_frame / max(total, 1)


def _corner_angles(quad: np.ndarray) -> np.ndarray:
    out = []
    for i in range(4):
        a, b = quad[i - 1] - quad[i], quad[(i + 1) % 4] - quad[i]
        cos = (a @ b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-9)
        out.append(np.degrees(np.arccos(np.clip(cos, -1, 1))))
    return np.array(out)


def _ramp(x: float, lo: float, hi: float) -> float:
    return float(np.clip((x - lo) / (hi - lo), 0, 1))


def detect_print(small_bgr: np.ndarray, cfg: dict) -> dict:
    """Locate the print in a reduced image. Corners are in the reduced image's pixel coordinates."""
    h, w = small_bgr.shape[:2]
    model = paper_model(small_bgr, cfg)
    full_frame = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    result = {"model": model, "quad": full_frame, "confidence": 0.0, "level": "low", "parts": {}, "flags": []}

    if model["surface"] is None:
        result.update(status="no_paper", flags=["no_paper_visible"])
        return result

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg["morph_kernel_px"],) * 2)
    content = cv2.morphologyEx((~model["paper"]).astype(np.uint8) * 255, cv2.MORPH_OPEN, k)
    content = cv2.morphologyEx(content, cv2.MORPH_CLOSE, k)
    contours, _ = cv2.findContours(content, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    big = [c for c in contours if cv2.contourArea(c) > cfg["min_content_fraction"] * h * w]
    if not big:
        result.update(status="no_content", flags=["no_print_found"])
        return result
    contour = max(big, key=cv2.contourArea)
    if len(big) > 1:
        result["flags"].append("multiple_regions")

    quad, fit_error = fit_quad(contour)
    quad = _order_corners(np.clip(quad, [0, 0], [w - 1, h - 1]).astype(np.float32))

    filled = np.zeros((h, w), np.uint8)
    cv2.drawContours(filled, [contour], -1, 255, cv2.FILLED)
    quad_mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(quad_mask, quad.astype(np.int32), 255)
    both = np.count_nonzero(filled & quad_mask)
    fill = both / max(np.count_nonzero(quad_mask), 1)      # how much of the outline is print
    cover = both / max(np.count_nonzero(filled), 1)        # how much of the print is inside the outline
    angle_dev = float(np.abs(_corner_angles(quad) - 90).max())
    diag = float(np.hypot(w, h))

    outside, support, unchecked = edge_support(quad, model["paper"])
    edge = _densify(quad, 1.0)
    near_frame = ((edge[:, 0] < 2) | (edge[:, 1] < 2) | (edge[:, 0] > w - 3) | (edge[:, 1] > h - 3)).mean()
    if near_frame > 0.05:
        result["flags"].append("print_touches_frame")

    parts = {
        "paper_outside": round(outside, 3), "edge_support": round(support, 3), "cover": round(cover, 4),
        "fill": round(fill, 4), "max_angle_deviation_deg": round(angle_dev, 2),
        "fit_error_px": round(fit_error, 2), "frame_contact": round(float(near_frame), 3), "edge_unchecked": round(unchecked, 3),
        "area_fraction": round(float(cv2.contourArea(quad)) / (h * w), 3),
        "paper_fraction": round(model["paper_fraction"], 3),
    }
    s = cfg["confidence"]
    confidence = (
        _ramp(outside, *s["outside_paper"]) * _ramp(support, *s["edge_support"]) * _ramp(cover, *s["cover"])
        * (1 - _ramp(angle_dev, *s["angle_deviation_deg"]))
        * (1 - _ramp(fit_error / diag, *s["fit_error_fraction"]))
        * (1 - s["frame_contact_penalty"] * _ramp(float(near_frame), 0.05, 0.5))
    )
    level = "high" if confidence >= s["high"] else "medium" if confidence >= s["medium"] else "low"
    result.update(status="found", quad=quad, confidence=round(float(confidence), 3), level=level, parts=parts)
    return result
