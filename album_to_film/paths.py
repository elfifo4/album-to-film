"""Project locations. The source folder is read-only: nothing here ever writes into it."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROJECT_FILE = CONFIG_DIR / "project.json"   # local to each machine, not in version control


def _source_dir() -> Path:
    if not PROJECT_FILE.exists():
        raise SystemExit(
            "config/project.json is missing. Copy config/project.example.json to config/project.json "
            "and set source_dir to the folder that holds your photo captures."
        )
    folder = Path(json.loads(PROJECT_FILE.read_text(encoding="utf-8"))["source_dir"]).expanduser()
    return folder if folder.is_absolute() else ROOT / folder


SOURCE_DIR = _source_dir()

CATALOG_DIR = ROOT / "catalog"
CATALOG_DB = CATALOG_DIR / "catalog.db"
LOG_DIR = CATALOG_DIR / "logs"
WORK_DIR = ROOT / "work"
PROCESSED_DIR = ROOT / "processed"
PREVIEWS_DIR = ROOT / "previews"
REVIEW_DIR = ROOT / "review"
REPORTS_DIR = ROOT / "reports"
EDITORIAL_DIR = ROOT / "editorial"
TIMELINE_DIR = ROOT / "timeline"
ASSETS_DIR = ROOT / "assets"
RENDERS_DIR = ROOT / "renders"
MODELS_DIR = ROOT / "models"

OUTPUT_DIRS = [
    CONFIG_DIR, CATALOG_DIR, LOG_DIR, WORK_DIR, PROCESSED_DIR, PREVIEWS_DIR, REVIEW_DIR,
    REPORTS_DIR, EDITORIAL_DIR, TIMELINE_DIR, ASSETS_DIR / "trailer", ASSETS_DIR / "full",
    RENDERS_DIR / "drafts", RENDERS_DIR / "final", MODELS_DIR,
]


def ensure_output_dirs() -> None:
    for d in OUTPUT_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def assert_not_in_source(path: Path) -> Path:
    """Guard used by every writer: refuse any output path inside the source folder."""
    resolved = Path(path).resolve()
    if resolved == SOURCE_DIR.resolve() or SOURCE_DIR.resolve() in resolved.parents:
        raise PermissionError(f"Refusing to write inside the read-only source folder: {resolved}")
    return resolved
