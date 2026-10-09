"""Title cards: short text screens at fixed places in a film (opening, start of a chapter, closing).

Cards are editorial data in editorial/titles.json. The same drawing code serves the renderer and the
preview in the review page, so the preview is exactly what the film will show.
"""
import hashlib
import json
import re
import unicodedata

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import catalog, paths

TITLES_FILE = paths.EDITORIAL_DIR / "titles.json"
IMAGES_DIR = paths.EDITORIAL_DIR / "title_images"      # pictures chosen as card backgrounds
IMAGE_NAME = re.compile(r"[0-9a-f]{16}\.jpg")
IMAGE_MAX_SIDE = 2560


def load() -> dict:
    return json.loads(TITLES_FILE.read_text(encoding="utf-8")) if TITLES_FILE.exists() else {}


def save(cards: dict) -> None:
    catalog.write_text_atomic(TITLES_FILE, json.dumps(cards, indent=1, ensure_ascii=False))


def store_image(data: bytes) -> str:
    """Keep a picture chosen for a card background and return its name.

    The name comes from the content, so choosing the same picture twice stores it once, and a picture
    is never replaced: undo can always go back to a card that used an earlier one.
    """
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("this file is not a picture the site can read (use JPEG, PNG or WebP)")
    scale = IMAGE_MAX_SIDE / max(image.shape[:2])
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    name = hashlib.sha256(data).hexdigest()[:16] + ".jpg"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.assert_not_in_source(IMAGES_DIR / name)
    if not target.exists():
        target.write_bytes(cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes())
    return name


def load_image(card: dict) -> np.ndarray | None:
    """The picture a card uses as its background, if it has one."""
    name = card.get("image") or ""
    if card.get("background") != "image" or not IMAGE_NAME.fullmatch(name) or not (IMAGES_DIR / name).exists():
        return None
    return cv2.imdecode(np.fromfile(IMAGES_DIR / name, np.uint8), cv2.IMREAD_COLOR)


def default_darkness() -> float:
    return round(1 - catalog.load_config("films.json")["titles"]["photo_background_brightness"], 2)


def slots(chapter_order: list[str]) -> list[dict]:
    """The places a card can go, in film order."""
    return ([{"slot": "opening", "label": "Opening title"}]
            + [{"slot": f"chapter:{c}", "label": f"Title for \"{c}\"", "chapter": c} for c in chapter_order]
            + [{"slot": "closing", "label": "Closing title"}])


def clean(card: dict, film_names: list[str]) -> dict:
    """Validated copy of a card coming from the review page."""
    image = card.get("image") if IMAGE_NAME.fullmatch(str(card.get("image") or "")) else None
    background = card.get("background") if card.get("background") in ("black", "photo", "image") else "black"
    darkness = default_darkness() if card.get("darkness") in (None, "") else float(card["darkness"])
    return {
        "text": str(card.get("text", "")).strip()[:120],
        "subtext": str(card.get("subtext", "")).strip()[:160],
        "seconds": float(min(max(float(card.get("seconds") or 3.0), 1.0), 12.0)),
        "background": background if image or background != "image" else "black",
        "image": image,
        "darkness": round(min(max(darkness, 0.0), 0.95), 2),     # how far a picture behind the text is darkened
        "films": [f for f in card.get("films", film_names) if f in film_names],
    }


def for_film(film: str) -> dict:
    """Cards used by one film, by slot. Cards without text are ignored."""
    return {slot: c for slot, c in load().items() if film in c.get("films", []) and c.get("text")}


def visual_order(text: str) -> str:
    """Reorder a line for drawing left to right when it contains right-to-left letters (Hebrew, Arabic).

    Hebrew needs no letter shaping, so reversing the line and restoring the direction of numbers and
    Latin words is enough. Doing it here keeps the result the same on every machine.
    """
    if not any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text):
        return text
    out, run = [], []
    ltr = lambda ch: unicodedata.bidirectional(ch) in ("L", "EN", "AN")
    joins = lambda ch: ch in ".,:/-'"                  # keeps 20.10.1982 or 18:30 together
    chars = list(text)
    for i, ch in enumerate(chars):
        inside = run and joins(ch) and i + 1 < len(chars) and ltr(chars[i + 1])
        if ltr(ch) or inside:
            run.append(ch)
        else:
            out.extend(reversed(run))
            run = []
            out.append({"(": ")", ")": "(", "[": "]", "]": "["}.get(ch, ch))
    out.extend(reversed(run))
    return "".join(reversed(out))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    cfg = catalog.load_config("films.json")["titles"]
    for entry in cfg["fonts_bold" if bold else "fonts"]:
        try:
            return ImageFont.truetype(entry["path"], size, index=entry.get("index", 0), layout_engine=ImageFont.Layout.BASIC)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _fitted(draw: ImageDraw.ImageDraw, text: str, size: int, max_width: int, bold: bool):
    while True:
        font = _font(size, bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width or size <= 12:
            return font, box
        size = int(size * 0.92)


def draw_card(card: dict, width: int, height: int, background: np.ndarray | None = None) -> np.ndarray:
    """One card as a BGR frame. `background` is a picture (BGR): blurred for the "photo" style, sharp for "image"."""
    cfg = catalog.load_config("films.json")["titles"]
    if card.get("background") in ("photo", "image") and background is not None:
        h, w = background.shape[:2]
        fill = max(width / w, height / h)
        bg = cv2.resize(background, None, fx=fill, fy=fill, interpolation=cv2.INTER_AREA)
        y, x = (bg.shape[0] - height) // 2, (bg.shape[1] - width) // 2
        bg = bg[y:y + height, x:x + width]
        if card["background"] == "photo":
            bg = cv2.GaussianBlur(bg, (0, 0), width / 30)
        darkness = card["darkness"] if card.get("darkness") is not None else default_darkness()
        base = (bg.astype(np.float32) * (1 - darkness)).astype(np.uint8)
    else:
        base = np.zeros((height, width, 3), np.uint8)
    image = Image.fromarray(cv2.cvtColor(base, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    colour = tuple(cfg["text_colour"])
    tidy = lambda t: t.replace("·", "|").replace("•", "|")       # separators the Hebrew fonts lack
    main, sub = visual_order(tidy(card.get("text", ""))), visual_order(tidy(card.get("subtext", "")))
    main_font, mb = _fitted(draw, main, int(height * cfg["main_size"]), int(width * 0.82), True)
    main_h = mb[3] - mb[1]
    sub_font = sb = None
    total = main_h
    if sub:
        sub_font, sb = _fitted(draw, sub, int(height * cfg["sub_size"]), int(width * 0.82), False)
        total += int(height * 0.075) + (sb[3] - sb[1])
    top = (height - total) // 2
    draw.text(((width - (mb[2] - mb[0])) // 2 - mb[0], top - mb[1]), main, font=main_font, fill=colour)
    if sub:
        rule_y = top + main_h + int(height * 0.036)
        half = int(width * 0.05)
        draw.line([(width // 2 - half, rule_y), (width // 2 + half, rule_y)], fill=tuple(cfg["rule_colour"]), width=max(1, height // 360))
        draw.text(((width - (sb[2] - sb[0])) // 2 - sb[0], top + main_h + int(height * 0.075) - sb[1]), sub, font=sub_font, fill=colour)
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
