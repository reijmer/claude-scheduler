from pathlib import Path

BASE_DIR = Path.home() / ".claude-scheduler"
DB_PATH = BASE_DIR / "scheduler.db"
RUNS_DIR = BASE_DIR / "runs"
LOCKS_DIR = BASE_DIR / "locks"
LOG_PATH = BASE_DIR / "scheduler.log"


def ensure_dirs() -> None:
    BASE_DIR.mkdir(exist_ok=True)
    RUNS_DIR.mkdir(exist_ok=True)
    LOCKS_DIR.mkdir(exist_ok=True)
