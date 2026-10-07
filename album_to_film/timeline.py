"""Timeline solver: turns a film's selection into exact timing, transitions and camera moves.

The timeline is a list of boundaries (cut points). Shot i owns the time between boundary i and i+1; a
transition is centred on each boundary. Shot lengths come from editorial constraints (preferred,
minimum, maximum, weight). Music, when there is any, only refines them: a boundary snaps to a nearby
beat when both neighbouring shots stay within their limits.

Output: timeline/<film>.resolved.json, everything the renderer needs and nothing it has to decide.
"""
import json

import cv2
import numpy as np

from . import catalog, orient, paths

FACES_FILE = paths.REPORTS_DIR / "faces.json"


# ---------------------------------------------------------------- durations

def fit_durations(preferred: list[float], lo: list[float], hi: list[float], total: float) -> list[float]:
    """Scale preferred durations to add up to `total`, keeping each within its own [lo, hi] where possible."""
    d = np.array(preferred, float)
    lo, hi = np.array(lo, float), np.array(hi, float)
    free = np.ones(len(d), bool)
    for _ in range(len(d) + 1):
        budget = total - d[~free].sum()
        if not free.any() or budget <= 0:
            break
        d[free] *= budget / d[free].sum()
        clamped = free & ((d < lo) | (d > hi))
        if not clamped.any():
            break
        d[clamped] = np.clip(d[clamped], lo[clamped], hi[clamped])
        free &= ~clamped
    return d.tolist()


def beat_times(tempo: dict, until: float) -> tuple[list[float], list[float]]:
    """Beats and bar starts (seconds) from a tempo map: an explicit list, or BPM plus first-beat offset."""
    if tempo.get("beats"):
        beats = [b for b in tempo["beats"] if b <= until]
    else:
        step, t, beats = 60.0 / tempo["bpm"], tempo.get("first_beat_offset", 0.0), []
        while t <= until:
            beats.append(round(t, 4))
            t += step
    per_bar = tempo.get("beats_per_bar", 4)
    return beats, tempo.get("bars") or beats[::per_bar]


def snap_to_beats(bounds: list[float], lo: list[float], hi: list[float], chapter_start: list[bool], tempo: dict,
                  max_shift: float) -> tuple[list[float], int]:
    """Move inner boundaries onto beats (bars for chapter changes) when both neighbours stay within limits."""
    beats, bars = beat_times(tempo, bounds[-1])
    out, snapped = list(bounds), 0
    for k in range(1, len(bounds) - 1):
        grid = bars if chapter_start[k] else beats
        if not grid:
            continue
        target = min(grid, key=lambda b: abs(b - bounds[k]))
        before, after = target - out[k - 1], bounds[k + 1] - target
        if abs(target - bounds[k]) <= max_shift and lo[k - 1] <= before <= hi[k - 1] and lo[k] <= after <= hi[k]:
            out[k] = target
            snapped += 1
    return out, snapped


# ---------------------------------------------------------------- where to look

def face_focus(photo_ids: list[str], results: dict) -> dict:
    """Faces per photo (cached): the point the camera favours and the box it should keep in frame."""
    cache = json.loads(FACES_FILE.read_text(encoding="utf-8")) if FACES_FILE.exists() else {}
    if not orient.FACE_MODEL.exists():
        return {i: cache.get(i, {"focus": [0.5, 0.45], "box": None}) for i in photo_ids}
    changed = False
    for pid in photo_ids:
        stamp = f"{results[pid]['output']['width']}x{results[pid]['output']['height']}"
        if cache.get(pid, {}).get("stamp") == stamp:
            continue
        img = cv2.imdecode(np.fromfile(paths.ROOT / results[pid]["output"]["processed"], np.uint8), cv2.IMREAD_REDUCED_COLOR_4)
        h, w = img.shape[:2]
        detector = cv2.FaceDetectorYN.create(str(orient.FACE_MODEL), "", (w, h), 0.7)
        _, faces = detector.detect(img)
        if faces is None:
            cache[pid] = {"stamp": stamp, "focus": [0.5, 0.45], "box": None, "faces": 0}
        else:
            x0, y0 = faces[:, 0].min(), faces[:, 1].min()
            x1, y1 = (faces[:, 0] + faces[:, 2]).max(), (faces[:, 1] + faces[:, 3]).max()
            area = faces[:, 2] * faces[:, 3]
            cx = float(((faces[:, 0] + faces[:, 2] / 2) * area).sum() / area.sum())
            cy = float(((faces[:, 1] + faces[:, 3] / 2) * area).sum() / area.sum())
            clip = lambda v: round(float(np.clip(v, 0, 1)), 4)
            cache[pid] = {"stamp": stamp, "focus": [clip(cx / w), clip(cy / h)], "faces": int(len(faces)),
                          "box": [clip(x0 / w), clip(y0 / h), clip(x1 / w), clip(y1 / h)]}
        changed = True
    if changed:
        catalog.write_text_atomic(FACES_FILE, json.dumps(cache, indent=1))
    return {i: cache[i] for i in photo_ids}


def plan_motion(width: int, height: int, canvas_aspect: float, style: str, flip: bool, cfg: dict, look: dict) -> dict:
    """Camera move for one shot, as start and end {cx, cy, zoom} in fractions of the photograph.

    Landscape prints fill the frame ("cover"); upright prints are shown whole over a blurred copy of
    themselves ("fit"). Zoom is relative to that base framing. The focus point comes from the faces.
    """
    aspect = width / height
    mode = "cover" if aspect >= 1.15 else "fit"
    fx, fy = look["focus"]
    z = cfg["zoom"]
    if mode == "fit":
        style = "pull_out" if style == "pull_out" else "push_in"      # no sideways travel for a framed print
        near = {"cx": 0.5 + (fx - 0.5) * 0.25, "cy": 0.5 + (fy - 0.5) * 0.25, "zoom": 1 + z}
        wide = {"cx": 0.5, "cy": 0.5, "zoom": 1.0}
        start, end = (wide, near) if style == "push_in" else (near, wide)
    elif style == "pan":
        # Cover framing of a 3:2 print on 16:9 crops top and bottom, so the free travel is vertical and,
        # once zoomed, horizontal too. Travel a share of that slack, passing through the faces.
        zoom = 1 + z
        slack_x = 1 - 1 / zoom
        slack_y = 1 - (aspect / canvas_aspect) / zoom
        dx, dy = cfg["pan"] * slack_x / 2, cfg["pan"] * max(slack_y, 0) / 2
        sign = -1 if flip else 1
        start = {"cx": fx - sign * dx, "cy": fy + sign * dy * 0.5, "zoom": zoom}
        end = {"cx": fx + sign * dx, "cy": fy - sign * dy * 0.5, "zoom": zoom}
    else:
        wide = {"cx": 0.5 + (fx - 0.5) * 0.3, "cy": fy, "zoom": 1.0}
        near = {"cx": fx, "cy": fy, "zoom": 1 + z}
        start, end = (wide, near) if style == "push_in" else (near, wide)
    return {"mode": mode, "style": style, "from": start, "to": end}


# ---------------------------------------------------------------- solve

def solve(film: str, photo_ids: list[str] | None = None, target_seconds: float | None = None,
          tempo: dict | None = None, name: str | None = None) -> dict:
    """Resolve one film. `photo_ids` and `target_seconds` override the selection (used for pilots)."""
    cfg = catalog.load_config("films.json")
    prof, out_cfg = cfg["films"][film], cfg["output"]
    results = {r["id"]: r for r in json.loads((paths.REPORTS_DIR / "restore_results.json").read_text(encoding="utf-8"))}
    selection = json.loads((paths.EDITORIAL_DIR / "selection.json").read_text(encoding="utf-8"))[film]
    photos = json.loads((paths.EDITORIAL_DIR / "photos.json").read_text(encoding="utf-8"))
    editorial = {p["id"]: p for p in photos}
    ids = photo_ids or selection["order"]
    if tempo is None:
        tempo_file = paths.TIMELINE_DIR / f"{film}.tempo.json"
        tempo = json.loads(tempo_file.read_text(encoding="utf-8")) if tempo_file.exists() and not photo_ids else {}
    total = float(target_seconds or tempo.get("duration") or prof["target_seconds"])

    chapters = [results[i]["category"] for i in ids]
    sizes = {c: chapters.count(c) for c in set(chapters)}
    preferred, lo, hi = [], [], []
    for pid, chapter in zip(ids, chapters):
        e = editorial.get(pid, {})
        weight = float(e.get("duration_weight") or 1.0) * cfg["emphasis_duration_weight"].get(str(e.get("emotional_score") or 3), 1.0)
        if sizes[chapter] <= cfg["bridge_chapter_max_photos"]:
            weight *= prof["bridge_duration_weight"]
        preferred.append(float(e.get("preferred_duration") or prof["nominal_shot_seconds"]) * weight)
        lo.append(float(e.get("min_duration") or prof["min_shot_seconds"]))
        hi.append(float(e.get("max_duration") or prof["max_shot_seconds"]))
    durations = fit_durations(preferred, lo, hi, total)
    bounds = [0.0] + np.cumsum(durations).round(4).tolist()
    chapter_start = [False] + [chapters[k] != chapters[k - 1] for k in range(1, len(ids))] + [False]
    snapped = 0
    if tempo.get("bpm") or tempo.get("beats"):
        bounds, snapped = snap_to_beats(bounds, lo, hi, chapter_start, tempo, tempo.get("max_snap_seconds", 0.35))

    looks = face_focus(ids, results)
    canvas_aspect = out_cfg["aspect"][0] / out_cfg["aspect"][1]
    tr = prof["transition"]
    shots = []
    for k, pid in enumerate(ids):
        r = results[pid]
        if k == 0:
            t_in = {"type": "fade_from_black", "seconds": prof["fade_in_seconds"]}
        elif chapter_start[k]:
            t_in = {"type": tr["chapter"], "seconds": tr["chapter_seconds"]}
        else:
            t_in = {"type": (editorial.get(pid, {}).get("transition_in") or tr["default"]), "seconds": tr["seconds"]}
        own = bounds[k + 1] - bounds[k]
        t_in["seconds"] = round(min(t_in["seconds"], own * 0.8, (bounds[k] - bounds[k - 1]) * 0.8 if k else own), 3)
        style = editorial.get(pid, {}).get("motion_style") or prof["motion"]["styles"][k % len(prof["motion"]["styles"])]
        shots.append({
            "id": pid, "capture_order": r["capture_order"], "chapter": chapters[k], "path": r["output"]["processed"],
            "width": r["output"]["width"], "height": r["output"]["height"],
            "start": round(bounds[k], 4), "end": round(bounds[k + 1], 4), "transition_in": t_in,
            "motion": plan_motion(r["output"]["width"], r["output"]["height"], canvas_aspect, style, k % 2 == 1,
                                  prof["motion"], looks[pid]),
            "faces": looks[pid].get("faces", 0),
        })
    for k, s in enumerate(shots):       # each shot stays on screen until the next transition has finished
        nxt = shots[k + 1]["transition_in"]["seconds"] if k + 1 < len(shots) else 0.0
        own_in = s["transition_in"]["seconds"] if k else 0.0
        s["visible_start"] = round(s["start"] - own_in / 2, 4)
        s["visible_end"] = round(s["end"] + nxt / 2, 4)

    timeline = {
        "film": film, "name": name or film, "label": prof["label"], "fps": out_cfg["fps"], "aspect": out_cfg["aspect"],
        "duration": round(bounds[-1], 4), "fade_out_seconds": prof["fade_out_seconds"],
        "tempo": tempo, "boundaries_on_beats": snapped,
        "audio": [],      # later: [{"path", "start", "gain", "fade_in", "fade_out", "duck": [...]}]
        "shots": shots,
    }
    catalog.write_text_atomic(paths.TIMELINE_DIR / f"{timeline['name']}.resolved.json", json.dumps(timeline, indent=1, ensure_ascii=False))
    return timeline


def pilot_ids(film: str, per_chapter: int = 3) -> list[str]:
    """A short cut that shows every chapter change: the end of each chapter and the start of the next."""
    selection = json.loads((paths.EDITORIAL_DIR / "selection.json").read_text(encoding="utf-8"))[film]["order"]
    results = {r["id"]: r for r in json.loads((paths.REPORTS_DIR / "restore_results.json").read_text(encoding="utf-8"))}
    groups: list[list[str]] = []
    for pid in selection:
        if groups and results[groups[-1][-1]]["category"] == results[pid]["category"]:
            groups[-1].append(pid)
        else:
            groups.append([pid])
    picked = []
    for n, g in enumerate(groups):
        picked += g[:per_chapter] if n == len(groups) - 1 else g[-per_chapter:] if n == 0 else g[:per_chapter]
    return picked


def run(film: str) -> None:
    t = solve(film)
    lengths = [s["end"] - s["start"] for s in t["shots"]]
    m, s = divmod(round(t["duration"]), 60)
    print(f"timeline {film}: {len(t['shots'])} shots, {m}:{s:02d}, shot length {min(lengths):.1f}-{max(lengths):.1f}s "
          f"(median {np.median(lengths):.1f}s), boundaries on beats: {t['boundaries_on_beats']}")
