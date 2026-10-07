"""Music analysis: length, tempo and beat times of a song, with plain NumPy.

The result is cached in timeline/music_analysis.json and feeds the timeline solver, which snaps cuts
to nearby beats. Which song a film uses, and which photograph should meet which moment of the song,
is editorial data in editorial/music.json.
"""
import hashlib
import json
import shutil
import subprocess

import numpy as np

from . import catalog, paths

MUSIC_FILE = paths.EDITORIAL_DIR / "music.json"
ANALYSIS_FILE = paths.TIMELINE_DIR / "music_analysis.json"
SAMPLE_RATE, WINDOW, HOP = 22050, 2048, 512


def decode(path) -> np.ndarray:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("FFmpeg is required to read the song but was not found on this machine.")
    pcm = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, np.float32)


def onset_strength(samples: np.ndarray) -> np.ndarray:
    """How much new sound starts in each short frame (spectral flux), normalised."""
    frames = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP] * np.hanning(WINDOW).astype(np.float32)
    spectrum = np.log1p(50 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(np.diff(spectrum, axis=0), 0).sum(axis=1)
    return (flux - flux.mean()) / (flux.std() + 1e-9)


def estimate_bpm(flux: np.ndarray, low: float = 70, high: float = 140) -> float:
    rate = SAMPLE_RATE / HOP
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lags = np.arange(1, len(ac))
    bpm = 60 * rate / lags
    ok = (bpm >= low) & (bpm <= high)
    return float(bpm[ok][np.argmax(ac[1:][ok])])


def track_beats(flux: np.ndarray, bpm: float, tightness: float = 100.0) -> list[float]:
    """Beat times that follow the onsets while staying close to the tempo (dynamic programming).

    A fixed grid would drift on a recording played by people; this lets the beat breathe with the band.
    """
    rate = SAMPLE_RATE / HOP
    period = rate * 60 / bpm
    score, back = flux.copy(), np.full(len(flux), -1)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offsets = np.arange(lo, hi + 1)
    penalty = -tightness * np.log(offsets / period) ** 2
    for t in range(hi, len(flux)):
        candidates = score[t - offsets] + penalty
        best = int(np.argmax(candidates))
        if candidates[best] > 0:
            score[t] += candidates[best]
            back[t] = t - offsets[best]
    t = int(np.argmax(score[-int(period):])) + len(score) - int(period)
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return [round(float(b * HOP / SAMPLE_RATE), 3) for b in reversed(beats)]


def analyze(path) -> dict:
    """Length, tempo and beats of one audio file, cached by content hash."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    cache = json.loads(ANALYSIS_FILE.read_text(encoding="utf-8")) if ANALYSIS_FILE.exists() else {}
    if digest in cache:
        return cache[digest]
    samples = decode(path)
    flux = onset_strength(samples)
    bpm = estimate_bpm(flux)
    beats = track_beats(flux, bpm)
    cache[digest] = {"file": path.name, "duration": round(len(samples) / SAMPLE_RATE, 3), "bpm": round(bpm, 1),
                     "beats_per_bar": 4, "beats": beats}
    catalog.write_text_atomic(ANALYSIS_FILE, json.dumps(cache, ensure_ascii=False))
    return cache[digest]


def for_film(film: str) -> dict | None:
    """The song, its beats and this film's music settings; None when no music is configured."""
    if not MUSIC_FILE.exists():
        return None
    config = json.loads(MUSIC_FILE.read_text(encoding="utf-8"))
    settings = config.get("films", {}).get(film)
    if not settings or not config.get("file"):
        return None
    path = paths.ROOT / config["file"]
    if not path.exists():
        raise SystemExit(f"The song named in editorial/music.json was not found: {config['file']}")
    return {**analyze(path), "path": config["file"], **settings}


def run() -> None:
    config = json.loads(MUSIC_FILE.read_text(encoding="utf-8")) if MUSIC_FILE.exists() else {}
    if not config.get("file"):
        raise SystemExit("No song configured. Create editorial/music.json with a \"file\" entry.")
    a = analyze(paths.ROOT / config["file"])
    gaps = np.diff(a["beats"])
    m, s = divmod(round(a["duration"]), 60)
    print(f"music: {a['file']}  {m}:{s:02d}  about {a['bpm']} BPM, {len(a['beats'])} beats "
          f"(spacing {np.median(gaps):.3f}s, {np.percentile(gaps, 5):.3f}-{np.percentile(gaps, 95):.3f}s)")
