"""Nightly journal backups: a consistent SQLite snapshot plus a markdown copy.

Uses ``sqlite3.Connection.backup()`` (online, WAL-safe) so the running service
never has to stop. Each run writes ``journal-YYYYMMDD-HHMM.db`` and ``.md``
into ``journal.backup_dir`` and prunes to the newest ``journal.backup_keep``
pairs. On a Pi, point the backup dir at the same SSD/USB mount as the DB — the
point is SD-card wear and accidental deletion, not hardware failure.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import export
from .db import JournalDB

log = logging.getLogger(__name__)
PREFIX = "journal-"


def backup(db: JournalDB, dest_dir: str | Path, keep: int = 14, now: datetime | None = None) -> dict:
    """Snapshot ``db`` into ``dest_dir``; returns the paths written and what was pruned."""
    now = now or datetime.now(timezone.utc)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M")
    db_path = dest / f"{PREFIX}{stamp}.db"
    md_path = dest / f"{PREFIX}{stamp}.md"

    dst = sqlite3.connect(db_path)
    try:
        db.conn.backup(dst)       # pages copied under SQLite's own locking; WAL-consistent
        # The source's WAL setting travels with the pages; switch the snapshot to
        # a rollback journal so it is ONE file (no -wal/-shm sidecars to lose).
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
    for side in (".db-wal", ".db-shm"):
        sidecar = db_path.with_suffix(side)
        if sidecar.exists():
            sidecar.unlink()
    md_path.write_text(export.to_markdown(db.list_trades(), title="Trading journal backup"), encoding="utf-8")

    pruned = prune(dest, keep)
    return {"db": str(db_path), "md": str(md_path), "bytes": db_path.stat().st_size, "pruned": pruned}


def prune(dest: Path, keep: int) -> list[str]:
    """Delete all but the newest ``keep`` snapshots (by name = timestamp)."""
    snaps = sorted(p for p in dest.glob(f"{PREFIX}*.db"))
    removed: list[str] = []
    for old in snaps[:-keep] if keep > 0 else []:
        for path in (old, old.with_suffix(".md")):
            if path.exists():
                path.unlink()
                removed.append(str(path))
    return removed


def verify(db_path: str | Path) -> dict:
    """Open a snapshot read-only and count its rows — proves it is a usable database."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("signals", "trades", "tags", "rules", "ai_analysis", "memories")}
    finally:
        conn.close()
    return {"integrity": ok, **counts}


def next_run(time_hhmm: str, now: datetime | None = None) -> datetime:
    """Next UTC datetime at ``HH:MM`` strictly after ``now``."""
    h, m = (int(x) for x in time_hhmm.strip().split(":"))
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"bad backup_time {time_hhmm!r}")
    now = now or datetime.now(timezone.utc)
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=1)
    return cand


async def run_scheduler(cfg, db: JournalDB) -> None:
    """Background task: one backup per day at ``journal.backup_time`` UTC."""
    jc = cfg.journal
    if not jc.backup_dir:
        return
    while True:
        when = next_run(jc.backup_time)
        log.info("backup scheduler: next journal backup at %s UTC → %s", when.strftime("%Y-%m-%d %H:%M"), jc.backup_dir)
        await asyncio.sleep(max(1.0, (when - datetime.now(timezone.utc)).total_seconds()))
        try:
            out = backup(db, jc.backup_dir, jc.backup_keep)
            log.info("journal backup: %s (%d bytes), pruned %d", out["db"], out["bytes"], len(out["pruned"]))
        except Exception:  # noqa: BLE001 — a failed backup never kills the service
            log.exception("journal backup failed")
