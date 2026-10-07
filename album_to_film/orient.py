"""Stage 5: which way up is the photograph?

Evidence, strongest first: the rotation you already applied in Google Photos (the `-edited` variant),
then upright faces. Without either, the photo is left as is and flagged for review.
"""
import cv2
import numpy as np

from . import paths

FACE_MODEL = paths.MODELS_DIR / "face_detection_yunet_2023mar.onnx"
_detector = None


def _faces_score(bgr: np.ndarray, min_score: float) -> tuple[float, int]:
    global _detector
    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (320, 320), min_score)
    h, w = bgr.shape[:2]
    _detector.setInputSize((w, h))
    _, faces = _detector.detect(bgr)
    if faces is None:
        return 0.0, 0
    return float(faces[:, -1].sum()), len(faces)


def faces_vote(bgr: np.ndarray, cfg: dict) -> dict | None:
    """Try the four right-angle rotations and report which shows the most confident upright faces."""
    if not FACE_MODEL.exists():
        return None
    scale = cfg["face_long_side"] / max(bgr.shape[:2])
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    scores, counts = [], []
    for k in range(4):
        s, n = _faces_score(np.ascontiguousarray(np.rot90(small, -k)), cfg["face_min_score"])
        scores.append(round(s, 2))
        counts.append(n)
    order = np.argsort(scores)[::-1]
    best, second = scores[order[0]], scores[order[1]]
    return {"rotation_cw_deg": int(order[0]) * 90, "scores": scores, "faces": counts[int(order[0])],
            "margin": round(best - second, 2), "decisive": best > 0 and best - second >= cfg["face_min_margin"]}


def decide(edit_rotation_deg: int | None, vote: dict | None) -> dict:
    """Combine the evidence into one rotation (clockwise degrees) with a confidence label."""
    flags = []
    if edit_rotation_deg is not None:
        if vote and vote["decisive"] and vote["rotation_cw_deg"] != edit_rotation_deg:
            flags.append("faces_disagree_with_your_edit")
        return {"rotation_cw_deg": edit_rotation_deg, "source": "your edit", "confidence": "high", "flags": flags}
    if vote and vote["decisive"]:
        return {"rotation_cw_deg": vote["rotation_cw_deg"], "source": "faces", "confidence": "medium", "flags": flags}
    return {"rotation_cw_deg": 0, "source": "unchanged", "confidence": "low", "flags": ["uncertain_orientation"]}
