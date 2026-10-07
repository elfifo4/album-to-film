# Parents' Henna + Wedding, 1982

My parents were married in 1982. The henna and the wedding live on in a few hundred printed photographs
and one wedding invitation. In 2019 I photographed every print with my phone, each one laid on a white
sheet of paper on a table. The captures are honest but rough: paper and table around the print, skew and
perspective, sideways and upside-down frames, uneven light, faded colour.

This project turns those captures into something the family can watch.

## What we want in the end

Two short films cut from one restored library of photographs:

- **A trailer**, 45 to 90 seconds: only the strongest moments, quicker cuts, built to make people want more.
- **A full movie**, several minutes: the whole story with room to breathe.

Both follow the same arc: **henna → the wedding invitation → the wedding**. The invitation is the bridge
between the two celebrations.

The films should feel warm, nostalgic and cinematic: slow pushes and pans, gentle dissolves, nothing that
upstages forty-year-old photographs. A song will be chosen later, so timing is kept flexible enough to be
fitted to music once there is one.

## Principles

- **The originals are never touched.** The source folder is read-only. Every writer in the code refuses
  paths inside it, and `verify` re-hashes all source files against the catalog after each run.
- **Authentic, not "improved".** Restoration is conservative: crop, straighten, even out the lighting,
  gentle levels. No invented detail, no face beautification, no generative restoration.
- **Deterministic and local.** Ordinary computer-vision code does the work on this machine. An AI
  assistant helped design and write the pipeline and looks only at exceptional cases, not at every photo.
- **A human decides.** Anything uncertain goes to a review page; manual decisions always win over the
  automatic result and are never overwritten.
- **Resumable.** Each stage records what it did per file and skips work whose inputs, code version and
  settings have not changed.

## Privacy

This repository is public and contains **code, configuration and documentation only**. The photographs,
everything generated from them (crops, previews, catalog, reports, review decisions) and any future music
or video are excluded by `.gitignore` and stay on the family's machine.

## How it works

| Stage | What it does | Status |
|---|---|---|
| `ingest` | Inventories the captures: hashes, dimensions, EXIF, Google Takeout sidecars, original/edited pairs, capture order | done |
| `analyze` | Measures each capture (contrast, sharpness, paper, glare) and works out what each Google Photos edit changed | done |
| `browse` | Thumbnail index in capture order, used to label henna / invitation / wedding as a few number ranges | done |
| `pilot-select` | Picks a small representative pilot set from the measurements | done |
| `pilot-run` | Finds the print, corrects perspective, orients, enhances; pilot set only | done |
| `review` | Local review page: click four corners, rotate, choose enhancement, exclude, note; with undo | done |
| full-album run | The same restoration for every photograph | next |
| duplicates | Groups repeated captures of the same print (flag only, nothing deleted) | planned |
| editorial | Scores and separate selections for the trailer and the full movie | planned |
| timeline | Shot durations with min/max/weight, refined (not dictated) by the music's beats | planned |
| render | Ken Burns motion and transitions, drawn frame by frame and encoded with FFmpeg | planned |

### Finding the print

The white paper is modelled as a smooth brightness surface, because the lighting across the table is
uneven. Whatever departs from that surface is the print. A four-sided outline is fitted to it side by
side, using only the parts of the outline that are real edges, so a white dress touching the border of the
photograph does not pull the crop inward. Every result carries a confidence built from edge evidence:

- **high**: cropped automatically
- **medium**: cropped and queued for review
- **low**: left uncropped until corners are set by hand

The crop keeps the whole print with a hairline outward margin. It trims inward only where a strip just
inside the detected edge is almost entirely paper.

### Which way is up

The phone was held flat over the table, so its orientation tag is unreliable. The best evidence turned out
to be the rotations already applied by hand in Google Photos years ago: the `-edited` copies give the
correct orientation for most photographs. A small face detector cross-checks them and covers the rest.

### Enhancement

The paper around each print doubles as a reference for the colour and unevenness of the room light. It is
treated as an estimate, so corrections drawn from it are partial and capped. Two levels are offered per
photograph, `light` and `standard`, plus `none`. All strengths live in `config/enhancement.json`.

### Henna or wedding

No model is used for this. The prints were photographed album by album, so labelling a handful of
capture-order ranges by hand classified the whole collection.

## Running it

Requires Python 3 and, later, FFmpeg. Nothing is installed globally.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Put the captures in the source folder named in `wedding/paths.py`, then:

```bash
.venv/bin/python -m wedding ingest        # inventory the source folder (read-only)
.venv/bin/python -m wedding analyze       # measurements and original-vs-edited comparison
.venv/bin/python -m wedding browse        # previews/browse/index.html, thumbnails in capture order
.venv/bin/python -m wedding pilot-select  # choose the pilot set
.venv/bin/python -m wedding pilot-run     # restore the pilot set
.venv/bin/python -m wedding review        # review page on http://127.0.0.1:8765
.venv/bin/python -m wedding verify        # confirm the source folder is unchanged
```

The optional face cross-check uses OpenCV's YuNet model (232 KB), which is not stored in this repository:

```bash
curl -L -o models/face_detection_yunet_2023mar.onnx https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```

Without it, orientation relies on the hand-made rotations and on review.

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
| `wedding/` | pipeline code | yes |
| `config/` | thresholds and enhancement strengths | yes |
| `editorial/event_ranges.json` | which capture-order ranges are henna, invitation, wedding | yes |
| source folder, `processed/`, `previews/`, `renders/`, `assets/` | photographs, restored images, video, music | no |
| `catalog/`, `reports/`, `review/`, `work/` | catalog, reports and review decisions derived from the photographs | no |
| `models/` | downloaded model files | no |

## License

The code is released under the [MIT License](LICENSE). The license covers the code in this repository
only; the family photographs are not part of it and are not licensed for any use.
