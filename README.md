# album-to-film

Turn an old photo album into a film.

You photograph each printed photograph with your phone. This project finds the print inside every
capture, straightens it, turns it the right way up, gently restores it, lets you review the results, and
will then cut the restored photographs into short films with slow, cinematic motion and music.

Everything runs locally. Your photographs never leave your machine.

## The story behind it

My parents were married in 1982. Their henna and their wedding live on in about two hundred printed
photographs and one wedding invitation. In 2019 I photographed every print with my phone, each one laid on
a white sheet of paper on a table. The captures are honest but rough: paper and table around the print,
skew and perspective, sideways and upside-down frames, uneven light, faded colour.

I wanted to give the family something to watch, not a folder to scroll through. So this is being built
for that album first, and written so that it can serve any album photographed the same way.

## What it produces in the end

Two films from one restored library of photographs:

- **A trailer**, 45 to 90 seconds: only the strongest moments, quicker cuts, made to leave people wanting more.
- **A full movie**, several minutes: the whole story with room to breathe.

Photographs are grouped into chapters that you name. For my parents' album the arc is
henna → the wedding invitation → the wedding, with the invitation as the bridge between the two
celebrations.

The films should feel warm, nostalgic and cinematic: slow pushes and pans, gentle dissolves, nothing that
upstages old photographs. Timing is kept flexible so it can be fitted to a song chosen later.

## Principles

- **The originals are never touched.** The source folder is read-only. Every writer in the code refuses
  paths inside it, and `verify` re-hashes all source files against the catalog.
- **Authentic, not "improved".** Restoration is conservative: crop, straighten, even out the lighting,
  gentle levels. No invented detail, no face beautification, no generative restoration.
- **Deterministic and local.** Ordinary computer-vision code does the work. An AI assistant helped design
  and write the pipeline and looks only at exceptional cases, not at every photograph.
- **A human decides.** Anything uncertain goes to a review page. Manual decisions always win over the
  automatic result and are never overwritten.
- **Resumable.** Each stage records what it did per file and skips work whose inputs, code version and
  settings have not changed.

## Privacy

This repository contains **code, configuration and documentation only**. Its `.gitignore` is an
allow-list: everything is ignored unless explicitly named, so photographs, restored images, catalogs,
reports, review decisions, music and video cannot be committed by accident, whatever folder they are in.

## How it works

| Stage | What it does | Status |
|---|---|---|
| `ingest` | Inventories the captures: hashes, dimensions, EXIF, Google Takeout sidecars, original/edited pairs, capture order | done |
| `analyze` | Measures each capture (contrast, sharpness, paper, glare) and works out what each earlier edit changed | done |
| `browse` | Thumbnail index in capture order, used to label chapters as a few ranges of numbers | done |
| `pilot-select` | Picks a small representative pilot set from the measurements | done |
| `pilot-run` | Finds the print, corrects perspective, orients, enhances; pilot set only | done |
| `restore` | The same restoration for every photograph, resumable | done |
| `review` | Local review page with a "needs review" queue: click four corners with a magnifier, rotate, choose enhancement, exclude, note; with undo | done |
| Render tab | In the review page: render a quick preview or 1080p of each film from the current state, with progress and cancel, watch it in a built-in player, and keep dated final copies | done |
| `dedupe` | Groups repeated captures of the same print and tells them apart from similar shots of one scene (flag only, nothing deleted) | done |
| editorial | Per-photo scores and a separate selection for each film, from the profiles in `config/films.json`; adjustable in the review page's Films tab (stars, In / Auto / Out) | done |
| `timeline` | Resolves each film into exact cut points, transitions and camera moves. Shot lengths come from editorial constraints (preferred, min, max, weight); a tempo map only snaps cuts to nearby beats within those limits | done |
| `render` | Ken Burns motion, crossfades and chapter dips, drawn frame by frame with sub-pixel precision and encoded with FFmpeg together with the song | done |
| `music` | Analyses a song (length, tempo, beats) with plain NumPy. A film can pin a photograph to a moment of the song, either by starting the song later or by fitting shot lengths; cuts then snap to beats | done |
| titles | Title cards at fixed places (opening, start of a chapter, closing), edited in the review page with a live preview; right-to-left text supported | done |

### Finding the print

The pipeline expects each print to lie on white paper. The paper is modelled as a smooth brightness
surface, because the light across a table is uneven. Whatever departs from that surface is the print. A
four-sided outline is fitted to it side by side, using only the parts of the outline that are real edges,
so something pale inside the photograph that touches its border (a white dress, a bright sky) does not
pull the crop inward. Every result carries a confidence built from edge evidence:

- **high**: cropped automatically
- **medium**: cropped and queued for review
- **low**: left uncropped until corners are set by hand

No paper is left around the print: each side is trimmed while a thin strip inside the detected edge is
mostly paper, then a small fixed inset removes the soft transition at the edge. Corners set by hand are
used exactly as clicked.

### Which way is up

A phone held flat over a table records an unreliable orientation tag. If you already rotated some captures
in Google Photos, the exported `-edited` copies carry the correct orientation and are used as the
strongest evidence. A small face detector cross-checks them and covers the rest; what remains uncertain
goes to review.

### Enhancement

The paper around each print doubles as a reference for the colour and unevenness of the room light. It is
treated as an estimate, so corrections drawn from it are partial and capped. Two levels are offered per
photograph, `light` and `standard`, plus `none`. All strengths live in `config/enhancement.json`.

### Chapters

No model is used to sort photographs into chapters. Albums are usually photographed in order, so labelling
a handful of capture-order ranges by hand classifies the whole collection.

## Running it

Requires Python 3 and, later, FFmpeg. Nothing is installed globally.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config/project.example.json config/project.json
```

Edit `config/project.json` so that `source_dir` points to the folder with your captures. Then:

```bash
.venv/bin/python -m album_to_film ingest        # inventory the source folder (read-only)
.venv/bin/python -m album_to_film analyze       # measurements and original-vs-edited comparison
.venv/bin/python -m album_to_film browse        # previews/browse/index.html, thumbnails in capture order
.venv/bin/python -m album_to_film pilot-select  # choose the pilot set
.venv/bin/python -m album_to_film pilot-run     # restore the pilot set
.venv/bin/python -m album_to_film restore       # restore every photograph
.venv/bin/python -m album_to_film dedupe        # group repeated captures of the same print
.venv/bin/python -m album_to_film timeline --film trailer          # resolve timing, transitions, camera moves
.venv/bin/python -m album_to_film render --film trailer --pilot    # short pilot in renders/drafts
.venv/bin/python -m album_to_film review        # review page on http://127.0.0.1:8765
.venv/bin/python -m album_to_film verify        # confirm the source folder is unchanged
```

The optional face cross-check uses OpenCV's YuNet model (232 KB), which is not stored in this repository:

```bash
curl -L -o models/face_detection_yunet_2023mar.onnx https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```

Without it, orientation relies on earlier hand-made rotations and on review.

### What it assumes about your captures

- JPEG files, one printed photograph per capture, on white paper.
- Capture order comes from the filename when it looks like `YYYYMMDD_HHMMSS.jpg`, otherwise from EXIF,
  otherwise from a Google Takeout sidecar.
- Files ending in `-edited` are treated as edited copies of the original with the same name.

Thresholds were tuned on one album so far; expect to adjust `config/thresholds.json` for yours.

## Dependencies

| Package | Used for |
|---|---|
| `opencv-python-headless` | decoding, contours, perspective warp, enhancement, face check, later frame compositing |
| `numpy` | array maths behind OpenCV, statistics |
| `Pillow` | EXIF reading, later title text |

Exact versions are in `requirements.lock`.

## Layout

| Path | Contents | In this repository |
|---|---|---|
| `album_to_film/` | pipeline code | yes |
| `config/` | thresholds, enhancement strengths, `project.example.json` | yes |
| `config/project.json` | where your captures live | no |
| `processed/`, `previews/`, `renders/`, `assets/` | restored images, previews, video, music | no |
| `catalog/`, `reports/`, `review/`, `editorial/`, `work/` | catalog, reports and decisions derived from your photographs | no |
| `models/` | downloaded model files | no |

## License

The code is released under the [MIT License](LICENSE). The license covers the code in this repository
only; no photographs are part of it.
