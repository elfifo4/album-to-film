"""Renderer: draws a resolved timeline frame by frame and pipes the frames to FFmpeg.

Every frame is a sub-pixel affine view of the photograph, so camera moves are smooth at any speed.
FFmpeg only encodes (and, later, mixes audio). The renderer decides nothing: all timing, transitions
and camera moves come from timeline/<name>.resolved.json.
"""
import json
import resource
import shutil
import subprocess
import time

import cv2
import numpy as np

from . import catalog, paths, timeline as timeline_mod

FIT_FRACTION = 0.94      # an upright print is shown whole, slightly inside the frame


def smoothstep(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


class ShotLayer:
    """One photograph prepared for a given canvas size; `frame(u)` draws it at progress u in 0..1."""

    def __init__(self, shot: dict, width: int, height: int, draft: bool):
        self.W, self.H, self.m = width, height, shot["motion"]
        img = cv2.imdecode(np.fromfile(paths.ROOT / shot["path"], np.uint8), cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        cover = self.m["mode"] == "cover"
        self.base = max(width / w, height / h) if cover else min(width / w, height / h) * FIT_FRACTION
        peak = self.base * max(self.m["from"]["zoom"], self.m["to"]["zoom"])
        # Work from a copy only slightly larger than it will ever appear: sharp, and no shimmer from heavy minification.
        self.k = min(1.0, peak * 1.15)
        self.src = cv2.resize(img, None, fx=self.k, fy=self.k, interpolation=cv2.INTER_AREA) if self.k < 1 else img
        self.interp = cv2.INTER_LINEAR if draft else cv2.INTER_CUBIC
        self.background = self.mask = None
        if not cover:
            fill = max(width / w, height / h)
            bg = cv2.resize(img, None, fx=fill, fy=fill, interpolation=cv2.INTER_AREA)
            y, x = (bg.shape[0] - height) // 2, (bg.shape[1] - width) // 2
            bg = cv2.GaussianBlur(bg[y:y + height, x:x + width], (0, 0), width / 36)
            self.background = (bg.astype(np.float32) * 0.42).astype(np.uint8)
            self.mask = np.full(self.src.shape[:2], 255, np.uint8)

    def frame(self, u: float) -> np.ndarray:
        a, b = self.m["from"], self.m["to"]
        zoom = a["zoom"] * (b["zoom"] / a["zoom"]) ** u                 # steady apparent zoom speed
        s = self.base * zoom / self.k                                     # canvas pixels per working pixel
        sh, sw = self.src.shape[:2]
        cx, cy = (a["cx"] + (b["cx"] - a["cx"]) * u) * sw, (a["cy"] + (b["cy"] - a["cy"]) * u) * sh
        if self.background is None:                                       # keep the window inside the photograph
            half_w, half_h = self.W / 2 / s, self.H / 2 / s
            cx = sw / 2 if 2 * half_w >= sw else min(max(cx, half_w), sw - half_w)
            cy = sh / 2 if 2 * half_h >= sh else min(max(cy, half_h), sh - half_h)
        M = np.float32([[s, 0, self.W / 2 - s * cx], [0, s, self.H / 2 - s * cy]])
        if self.background is None:
            return cv2.warpAffine(self.src, M, (self.W, self.H), flags=self.interp, borderMode=cv2.BORDER_REPLICATE)
        fg = cv2.warpAffine(self.src, M, (self.W, self.H), flags=self.interp, borderMode=cv2.BORDER_CONSTANT)
        alpha = cv2.warpAffine(self.mask, M, (self.W, self.H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        alpha = alpha[..., None].astype(np.uint16)                        # soft edge, so the print's border does not crawl
        return ((fg.astype(np.uint16) * alpha + self.background.astype(np.uint16) * (255 - alpha) + 127) // 255).astype(np.uint8)


def compose(t: float, tl: dict, layers: dict, width: int, height: int, draft: bool) -> np.ndarray:
    active = [s for s in tl["shots"] if s["visible_start"] <= t < s["visible_end"]]
    for pid in [p for p in layers if p not in {s["id"] for s in active}]:
        del layers[pid]                                                   # finished shots free their memory
    if not active:
        return np.zeros((height, width, 3), np.uint8)

    def draw(s):
        if s["id"] not in layers:
            layers[s["id"]] = ShotLayer(s, width, height, draft)
        return layers[s["id"]].frame((t - s["visible_start"]) / (s["visible_end"] - s["visible_start"]))

    if len(active) == 1:
        frame = draw(active[0])
    else:
        old, new = active[-2], active[-1]
        d = new["transition_in"]["seconds"]
        p = (t - (new["start"] - d / 2)) / d if d else 1.0
        if new["transition_in"]["type"] == "dip_to_black":
            frame = cv2.convertScaleAbs(draw(old), alpha=smoothstep(1 - 2 * p)) if p < 0.5 else \
                    cv2.convertScaleAbs(draw(new), alpha=smoothstep(2 * p - 1))
        else:
            mix = smoothstep(p)
            frame = cv2.addWeighted(draw(old), 1 - mix, draw(new), mix, 0)

    first = tl["shots"][0]["transition_in"]
    gain = min(smoothstep(t / first["seconds"]) if first["seconds"] else 1.0,
               smoothstep((tl["duration"] - t) / tl["fade_out_seconds"]) if tl["fade_out_seconds"] else 1.0)
    return frame if gain >= 0.999 else cv2.convertScaleAbs(frame, alpha=gain)


def render(tl: dict, out_path, height: int, draft: bool = False) -> dict:
    """Render one resolved timeline to an MP4. Returns measurements of the run."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("FFmpeg is required for rendering but was not found on this machine.")
    cfg = catalog.load_config("films.json")["output"]
    width = int(round(height * tl["aspect"][0] / tl["aspect"][1] / 2)) * 2
    fps, frames = tl["fps"], int(round(tl["duration"] * tl["fps"]))
    out_path = paths.assert_not_in_source(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    encoder = subprocess.Popen(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
         "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast" if draft else cfg["preset"],
         "-crf", str(cfg["crf"] + (5 if draft else 0)), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)],
        stdin=subprocess.PIPE)
    layers: dict = {}
    started, cpu0 = time.time(), time.process_time()
    for f in range(frames):
        encoder.stdin.write(compose(f / fps, tl, layers, width, height, draft).tobytes())
    encoder.stdin.close()
    if encoder.wait() != 0:
        raise SystemExit("FFmpeg failed while encoding.")
    wall = time.time() - started
    return {
        "name": tl["name"], "file": str(out_path.relative_to(paths.ROOT)), "size": f"{width}x{height}", "fps": fps,
        "frames": frames, "seconds_of_video": round(tl["duration"], 2), "render_seconds": round(wall, 1),
        "frames_per_second": round(frames / wall, 1), "realtime_factor": round(tl["duration"] / wall, 2),
        "python_cpu_seconds": round(time.process_time() - cpu0, 1),
        "python_peak_memory_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6),
        "encoder_peak_memory_mb": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1e6),
        "file_mb": round(out_path.stat().st_size / 1e6, 1), "draft": draft,
    }


def run(film: str, height: int, pilot: bool, draft: bool) -> dict:
    if pilot:
        prof = catalog.load_config("films.json")["films"][film]
        ids = timeline_mod.pilot_ids(film)
        tl = timeline_mod.solve(film, photo_ids=ids, target_seconds=round(len(ids) * prof["nominal_shot_seconds"] * 1.1, 1),
                                name=f"pilot_{film}")
    else:
        tl = timeline_mod.solve(film)
    out = paths.RENDERS_DIR / "drafts" / f"{tl['name']}_{height}p.mp4"
    stats = render(tl, out, height, draft)
    log = paths.REPORTS_DIR / "render_benchmark.json"
    history = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    catalog.write_text_atomic(log, json.dumps(history + [stats], indent=1))
    print(f"render {stats['name']} {stats['size']}: {stats['seconds_of_video']}s of video in {stats['render_seconds']}s "
          f"({stats['frames_per_second']} fps, {stats['realtime_factor']}x real time), "
          f"memory {stats['python_peak_memory_mb']} MB + encoder {stats['encoder_peak_memory_mb']} MB, "
          f"file {stats['file_mb']} MB -> {stats['file']}")
    return stats
