from __future__ import annotations

import sys
from pathlib import Path


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", APP_ROOT)).resolve()
FRONTEND_DIR = BUNDLE_ROOT / "frontend"
DATA_DIR = APP_ROOT / "data"
PAPERS_DIR = APP_ROOT / "papers"
DB_PATH = DATA_DIR / "papers.db"
AGENT_MD_PATH = APP_ROOT / "AGENT.md"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
