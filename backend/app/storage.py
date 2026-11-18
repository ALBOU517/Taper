"""
Where Taper keeps your data.

Everything lives in one folder, ``TAPER_DATA_DIR`` (default ``~/.taper``):

    ~/.taper/taper.db            the database (SQLite) — all your history
    ~/.taper/secret.key          signs your login; kept so restarts don't sign you out
    ~/.taper/backups/            a dated copy of the database, one per day, last 30 kept

The path is absolute, so starting the server from any directory opens the same
database. Nothing in here is ever deleted automatically except backups older
than the newest 30.
"""
from __future__ import annotations

import os
import secrets
import shutil
import sqlite3
from datetime import date
from pathlib import Path

DATA_DIR = Path(os.getenv("TAPER_DATA_DIR", Path.home() / ".taper")).expanduser().resolve()
BACKUP_DIR = DATA_DIR / "backups"
KEEP_BACKUPS = 30


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(exist_ok=True)


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url.replace("postgres://", "postgresql+psycopg://", 1) if url.startswith("postgres://") else url
    ensure_dirs()
    return f"sqlite:///{DATA_DIR / 'taper.db'}"


def secret_key() -> str:
    env = os.getenv("SECRET_KEY")
    if env:
        return env
    ensure_dirs()
    f = DATA_DIR / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_text().strip()


def sqlite_path(url: str) -> Path | None:
    return Path(url.removeprefix("sqlite:///")) if url.startswith("sqlite:///") else None


def daily_backup(url: str, today: date | None = None) -> Path | None:
    """Consistent copy of the SQLite database using SQLite's online backup API."""
    src = sqlite_path(url)
    if not src or not src.exists():
        return None
    ensure_dirs()
    today = today or date.today()
    dest = BACKUP_DIR / f"taper-{today.isoformat()}.db"
    if not dest.exists():
        with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
            a.backup(b)
    olds = sorted(BACKUP_DIR.glob("taper-*.db"))[:-KEEP_BACKUPS]
    for o in olds:
        o.unlink()
    return dest


def restore_hint() -> str:
    return f"To restore a backup, stop Taper and copy a file from {BACKUP_DIR} over {DATA_DIR / 'taper.db'}."


__all__ = ["DATA_DIR", "BACKUP_DIR", "database_url", "secret_key", "daily_backup", "restore_hint", "shutil"]
