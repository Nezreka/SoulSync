"""Verified, immutable snapshots before this generation's startup migrations."""

from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import uuid

from core import db_integrity
from utils.logging_config import get_logger

logger = get_logger("library2.upgrade_backup")


# Bump before introducing another startup migration that needs a new snapshot.
STARTUP_UPGRADE_VERSION = 1


@dataclass(frozen=True)
class StartupUpgrade:
    backup_path: Path | None


def prepare_startup_upgrade(database_path: Path) -> StartupUpgrade | None:
    """Read the completion marker, then snapshot populated pre-upgrade state.

    No database write (including a journal-mode change) precedes this function.
    A failed attempt gets a new snapshot on retry; no existing filename is
    assumed to be a complete or verified backup.
    """
    path = Path(database_path).resolve()
    populated = False
    if path.exists() and path.stat().st_size:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if 'lib2_startup_upgrades' in tables:
                if conn.execute('SELECT 1 FROM lib2_startup_upgrades WHERE version=?',
                                (STARTUP_UPGRADE_VERSION,)).fetchone():
                    return None
            # LIMIT 1 avoids scanning large libraries. Quote names read from
            # sqlite_master so an unusual user table name remains harmless.
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                if conn.execute(f'SELECT 1 FROM {quoted} LIMIT 1').fetchone():
                    populated = True
                    break
    if not populated:
        return StartupUpgrade(backup_path=None)

    directory = path.parent / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = directory / f'{path.stem}.pre-upgrade-v{STARTUP_UPGRADE_VERSION}-{uuid.uuid4().hex}.db'
    temporary = snapshot.with_suffix('.db.tmp')
    try:
        db_integrity.safe_backup(str(path), str(temporary), verify_source=True, verify_result=True)
        # Opened for writing: Windows refuses to flush a read-only handle
        # (EBADF), which failed every snapshot and so every upgrade there.
        with temporary.open('r+b') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, snapshot)
        # Make the published filename durable before schema changes can commit.
        if os.name != 'nt':
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except BaseException:
        with suppress(OSError):
            temporary.unlink()
        logger.error("Pre-upgrade snapshot of %s failed; the upgrade will not start without it "
                     "(free disk space in %s and restart)", path, directory)
        raise
    _prune_failed_attempts(directory, path.stem, keep=snapshot)
    return StartupUpgrade(backup_path=snapshot)


def _prune_failed_attempts(directory: Path, stem: str, *, keep: Path, retained: int = 2) -> None:
    """A schema failure rolls back, so every retry snapshots the same state; a
    restart loop must not fill the disk. Older attempts beyond ``retained`` go."""
    attempts = sorted(directory.glob(f'{stem}.pre-upgrade-v{STARTUP_UPGRADE_VERSION}-*.db'),
                      key=lambda candidate: candidate.stat().st_mtime, reverse=True)
    for stale in attempts[retained:]:
        if stale != keep:
            with suppress(OSError):
                stale.unlink()


def record_startup_upgrade(conn, upgrade: StartupUpgrade | None) -> None:
    """Record success in the caller's schema transaction, never before it."""
    if upgrade is None:
        return
    conn.execute('''CREATE TABLE IF NOT EXISTS lib2_startup_upgrades(
        version INTEGER PRIMARY KEY,
        backup_path TEXT,
        completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('INSERT INTO lib2_startup_upgrades(version,backup_path) VALUES(?,?)',
                 (STARTUP_UPGRADE_VERSION, str(upgrade.backup_path) if upgrade.backup_path else None))
