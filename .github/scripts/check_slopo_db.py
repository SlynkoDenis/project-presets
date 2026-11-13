#!/usr/bin/env python3
"""Discard a cached slopo database that belongs to another source tree."""

from __future__ import annotations

import os
import posixpath
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Final

DIRECTORY: Final[Path] = Path(".dealer-local/slopo")
DATABASE: Final[Path] = DIRECTORY / "slopo.db"


def main() -> None:
    source = os.environ.get("SOURCE_DIR")
    if not source:
        print("check-slopo-db: SOURCE_DIR is required", file=sys.stderr)
        raise SystemExit(1)

    parts = [part for part in posixpath.normpath(source).split("/") if part and part != "."]
    absolute = Path(source).resolve()
    keep = False
    if DATABASE.exists():
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(DATABASE)
            row = connection.execute("SELECT source_dir FROM metadata WHERE id = 1").fetchone()
            stored_value = row[0] if row is not None else None
            stored = (
                [part for part in posixpath.normpath(stored_value).split("/") if part]
                if isinstance(stored_value, str)
                else []
            )
            if row is not None and (not parts or stored[-len(parts) :] == parts):
                connection.execute(
                    "UPDATE metadata SET source_dir = ? WHERE id = 1",
                    (str(absolute),),
                )
                connection.commit()
                keep = True
        except sqlite3.Error:
            keep = False
        finally:
            if connection is not None:
                connection.close()
    if not keep:
        shutil.rmtree(DIRECTORY, ignore_errors=True)
    print("Reusing cached slopo database" if keep else "Rebuilding slopo database")


if __name__ == "__main__":
    main()
