"""
Temporary utility: wipe all photos, faces, and people from the local organizer database.

DBSCAN cluster ids are stored on ``faces.cluster_id`` (no separate clusters table).

Usage (from project root, with the FastAPI server stopped or idle):
    python reset_db.py
    python reset_db.py --db organizer.db --vacuum

To delete the database file entirely and recreate schema on next app start:
    del organizer.db
    python -c "from database import DatabaseManager; DatabaseManager().create_tables()"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from database import DatabaseManager, DEFAULT_DB_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete all rows from photos, faces, and people tables.",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run VACUUM after delete to reclaim disk space",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).resolve()

    if not db_path.is_file():
        print(f"Database file not found: {db_path}", file=sys.stderr)
        return 1

    manager = DatabaseManager(str(db_path))
    before = manager.get_schema_version_info()

    print(f"Database: {db_path}")
    print(
        "Before — "
        f"photos: {before['photos']}, "
        f"faces: {before['faces']}, "
        f"people: {before['people']}, "
        f"unassigned faces: {before['faces_unassigned']}, "
        f"unnamed clusters: {before['unnamed_clusters']}",
    )

    if not args.yes:
        answer = input(
            "Delete ALL photos, faces, and people? This cannot be undone. [y/N]: "
        ).strip()
        if answer.lower() not in {"y", "yes"}:
            print("Aborted.")
            return 0

    removed = manager.clear_all_ingestion_data()
    print(
        "Removed — "
        f"photos: {removed['photos']}, "
        f"faces: {removed['faces']}, "
        f"people: {removed['people']}",
    )

    if args.vacuum:
        manager.vacuum()
        print("VACUUM completed.")

    after = manager.get_schema_version_info()
    print(
        "After — "
        f"photos: {after['photos']}, "
        f"faces: {after['faces']}, "
        f"people: {after['people']}",
    )
    print("Done. You can run a fresh folder scan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
