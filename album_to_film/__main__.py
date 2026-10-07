"""Command line entry point:  .venv/bin/python -m album_to_film <stage>"""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="album_to_film", description="From phone captures of printed photographs to short films")
    sub = parser.add_subparsers(dest="stage", required=True)
    sub.add_parser("ingest", help="inventory the source folder into the catalog (read-only)")
    analyze = sub.add_parser("analyze", help="measure every photo and compare original vs edited")
    analyze.add_argument("--limit", type=int, help="only the first N files, for a quick test")
    sub.add_parser("verify", help="re-hash the source folder and confirm nothing changed")
    sub.add_parser("browse", help="build the capture-order thumbnail index")
    sub.add_parser("pilot-select", help="choose the pilot set and draw its contact sheet")
    sub.add_parser("pilot-run", help="restore the pilot files only")
    sub.add_parser("restore", help="detect, orient and enhance every photograph (resumable)")
    sub.add_parser("dedupe", help="group repeated captures of the same print (flag only)")
    tl = sub.add_parser("timeline", help="resolve a film's timing, transitions and camera moves")
    tl.add_argument("--film", required=True)
    rd = sub.add_parser("render", help="render a film (or a short pilot) to renders/drafts")
    rd.add_argument("--film", required=True)
    rd.add_argument("--height", type=int, default=1080, help="frame height in pixels, e.g. 540 or 1080")
    rd.add_argument("--pilot", action="store_true", help="a short cut across the chapter changes")
    rd.add_argument("--draft", action="store_true", help="faster, lower quality")
    sub.add_parser("music", help="analyse the configured song: length, tempo, beats")
    sub.add_parser("review", help="serve the interactive review page on 127.0.0.1:8765")
    args = parser.parse_args()

    if args.stage == "ingest":
        from . import ingest
        ingest.run()
    elif args.stage == "analyze":
        from . import analyze
        analyze.run(args.limit)
    elif args.stage == "verify":
        from . import ingest
        return 0 if ingest.verify() else 1
    elif args.stage == "browse":
        from . import browse
        browse.run()
    elif args.stage == "pilot-select":
        from . import pilot
        pilot.run()
    elif args.stage == "pilot-run":
        from . import process
        process.run("pilot")
    elif args.stage == "restore":
        from . import process
        process.run("all")
    elif args.stage == "dedupe":
        from . import dedupe
        dedupe.run()
    elif args.stage == "timeline":
        from . import timeline
        timeline.run(args.film)
    elif args.stage == "render":
        from . import render
        render.run(args.film, args.height, args.pilot, args.draft)
    elif args.stage == "music":
        from . import music
        music.run()
    elif args.stage == "review":
        from . import review
        review.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
