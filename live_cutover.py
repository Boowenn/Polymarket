#!/usr/bin/env python3
"""Archive the current database and clear non-live bot state from the active DB."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import config
import models


def vacuum_database(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


def main() -> None:
    models.init_db()

    db_path = Path(config.DB_PATH).resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    archive_dir = db_path.parent / "archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = archive_dir / f"{db_path.stem}_pre_live_cutover_{stamp}{db_path.suffix}"
    shutil.copy2(db_path, archive_path)

    summary = models.purge_non_live_state(clear_risk_logs=True, clear_pnl_logs=True)
    vacuum_database(db_path)

    print(f"ARCHIVE={archive_path}")
    for section in ("before", "after"):
        print(section.upper())
        for key, value in summary[section].items():
            print(f"  {key}={value}")


if __name__ == "__main__":
    main()
