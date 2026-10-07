"""Stage 6: turn a capture into a rectified, conservatively enhanced photograph.

Geometry is one perspective warp from the full-resolution capture. Every tonal step is capped by
config/enhancement.json. Three outputs are possible: geometry only, "light", and "standard".
"""
import cv2
import numpy as np


def capture_correction(small_bgr: np.ndarray, model: dict, cfg: dict) -> np.ndarray | None:
    """Per-pixel BGR gain (reduced size) that evens out the lighting and neutralises the light colour.

    The paper is only an estimate of the lighting, so both corrections are applied at partial strength
    and clamped.
    """
    if model["surface"] is None:
        return None
    p = cfg["paper_reference"]
    surface = np.maximum(model["surface"], 1.0)
    reference = float(np.median(surface[model["paper"]])) if model["paper"].any() else float(np.median(surface))
    flat = np.clip(reference / surface, 1 / p["max_flat_field_gain"], p["max_flat_field_gain"])
    flat = 1 + p["flat_field_strength"] * (flat - 1)
    gain = np.repeat(flat[..., None], 3, axis=2)
    if model["paper_bgr"] is not None:
        wb = np.clip(model["paper_bgr"].mean() / np.maximum(model["paper_bgr"], 1.0),
                     1 / p["max_channel_gain"], p["max_channel_gain"])
        gain *= 1 + p["white_balance_strength"] * (wb - 1)
    return gain.astype(np.float32)


def apply_gain(full_bgr: np.ndarray, gain_small: np.ndarray | None) -> np.ndarray:
    if gain_small is None:
        return full_bgr
    h, w = full_bgr.shape[:2]
    gain = cv2.resize(gain_small, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(full_bgr.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def expand_quad(quad: np.ndarray, margin: float, width: int, height: int) -> np.ndarray:
    """Grow the outline outward by `margin` of its size, where the frame allows."""
    c = quad.mean(axis=0)
    grown = c + (quad - c) * (1 + 2 * margin)
    return np.clip(grown, [0, 0], [width - 1, height - 1]).astype(np.float32)


def target_size(quad: np.ndarray) -> tuple[int, int]:
    tl, tr, br, bl = quad
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    return max(int(round(w)), 8), max(int(round(h)), 8)


def warp(image: np.ndarray, quad: np.ndarray, size: tuple[int, int], interpolation=cv2.INTER_LANCZOS4):
    w, h = size
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    H = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    return cv2.warpPerspective(image, H, (w, h), flags=interpolation, borderMode=cv2.BORDER_REPLICATE)


def evidence_trim(paper_warped: np.ndarray, margin: float, cfg: dict) -> dict:
    """Inward trim per side, only where a strip just inside the detected edge is still mostly paper.

    Returns fractions of the warped width/height to remove on each side (0 where there is no evidence).
    """
    h, w = paper_warped.shape
    need, step, limit = cfg["inward_evidence_min_paper_fraction"], cfg["inward_strip_fraction"], cfg["inward_max_fraction"]
    is_paper = paper_warped > 0
    trims = {}
    for side, length, strip in (("left", w, lambda a, b: is_paper[:, a:b]), ("right", w, lambda a, b: is_paper[:, w - b:w - a]),
                                ("top", h, lambda a, b: is_paper[a:b, :]), ("bottom", h, lambda a, b: is_paper[h - b:h - a, :])):
        start = cut = int(round(margin * length))
        width = max(1, int(round(step * length)))
        while cut - start < limit * length and strip(cut, cut + width).mean() > need:
            cut += width
        trims[side] = round(cut / length, 4) if cut > start else 0.0
    return trims


def apply_trim(image: np.ndarray, trims: dict) -> np.ndarray:
    h, w = image.shape[:2]
    return image[int(trims["top"] * h): h - int(trims["bottom"] * h), int(trims["left"] * w): w - int(trims["right"] * w)]


def _luma(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def levels(bgr: np.ndarray, cfg: dict) -> tuple[np.ndarray, dict]:
    """Gentle black/white point stretch, the same for all channels so colours keep their balance."""
    c = cfg["levels"]
    lo, hi = np.percentile(_luma(bgr), [c["black_percentile"], c["white_percentile"]])
    black_target = lo * (1 - c["black_strength"])
    white_target = hi + (c["white_target"] - hi) * c["white_strength"]
    scale = float(np.clip((white_target - black_target) / max(hi - lo, 1.0), 1.0, c["max_gain"]))
    out = np.clip((bgr.astype(np.float32) - lo) * scale + black_target, 0, 255).astype(np.uint8)
    return out, {"black": round(float(lo), 1), "white": round(float(hi), 1), "gain": round(scale, 3)}


def cast_correction(bgr: np.ndarray, cfg: dict) -> np.ndarray:
    """Partial grey-world correction of the print's own colour cast."""
    means = bgr.reshape(-1, 3).mean(axis=0)
    gains = np.clip(means.mean() / np.maximum(means, 1.0), 1 / cfg["max_cast_gain"], cfg["max_cast_gain"])
    gains = 1 + cfg["cast_correction_strength"] * (gains - 1)
    return np.clip(bgr.astype(np.float32) * gains, 0, 255).astype(np.uint8)


def local_contrast(bgr: np.ndarray, cfg: dict) -> np.ndarray:
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=cfg["clahe"]["clip_limit"], tileGridSize=(8, 8))
    lab[..., 0] = cv2.addWeighted(clahe.apply(lab[..., 0]), cfg["clahe"]["blend"], lab[..., 0], 1 - cfg["clahe"]["blend"], 0)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def unsharp(bgr: np.ndarray, cfg: dict) -> np.ndarray:
    blurred = cv2.GaussianBlur(bgr, (0, 0), cfg["unsharp"]["radius"])
    return cv2.addWeighted(bgr, 1 + cfg["unsharp"]["amount"], blurred, -cfg["unsharp"]["amount"], 0)


def clipped_fraction(bgr: np.ndarray) -> float:
    return float(((bgr >= 254).any(axis=2) | (bgr <= 1).all(axis=2)).mean())


def enhance(geom: np.ndarray, level: str, cfg: dict) -> tuple[np.ndarray, dict]:
    """`geom` already has the capture correction. 'light' adds levels; 'standard' adds cast, local contrast, sharpening."""
    if level == "none":
        return geom, {"level": "none"}
    out, info = levels(geom, cfg)
    if level == "standard":
        out = unsharp(local_contrast(cast_correction(out, cfg), cfg), cfg)
    info.update(level=level, clipped_before=round(clipped_fraction(geom), 4), clipped_after=round(clipped_fraction(out), 4))
    info["may_hurt"] = info["clipped_after"] - info["clipped_before"] > cfg["max_added_clipping"]
    return out, info
