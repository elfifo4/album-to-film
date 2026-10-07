"""Command line entry point:  .venv/bin/python -m wedding <stage>"""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="wedding", description="1982 henna + wedding photo pipeline")
    sub = parser.add_subparsers(dest="stage", required=True)
    sub.add_parser("ingest", help="inventory the source folder into the catalog (read-only)")
    analyze = sub.add_parser("analyze", help="measure every photo and compare original vs edited")
    analyze.add_argument("--limit", type=int, help="only the first N files, for a quick test")
    sub.add_parser("verify", help="re-hash the source folder and confirm nothing changed")
    sub.add_parser("browse", help="build the capture-order thumbnail index")
    sub.add_parser("pilot-select", help="choose the pilot set and draw its contact sheet")
    sub.add_parser("pilot-run", help="detect, orient and restore the pilot files and build the review page")
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
        process.run()
    elif args.stage == "review":
        from . import review
        review.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
