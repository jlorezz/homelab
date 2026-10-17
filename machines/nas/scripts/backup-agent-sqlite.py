"""Replace staged SQLite files with online-consistent backups, including WAL data."""

from pathlib import Path
import sqlite3
import sys
import tempfile


def backup_databases(source, staging):
    excluded = {"node_modules", ".cache", ".next", "dist", "result", "logs"}
    # Walk the already filtered staging tree, not dependency trees in the source.
    for target in staging.rglob("*"):
        if not target.is_file() or target.is_symlink():
            continue
        relative = target.relative_to(staging)
        if excluded.intersection(relative.parts):
            continue
        original = source / relative
        if not original.resolve().is_relative_to(source.resolve()):
            continue
        try:
            with original.open("rb") as stream:
                is_sqlite = stream.read(16) == b"SQLite format 3\x00"
        except FileNotFoundError:
            continue
        if not is_sqlite:
            continue
        # The copied workspace is untrusted: never follow a predictable temp symlink.
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
        try:
            connection = sqlite3.connect(original.resolve().as_uri() + "?mode=ro", uri=True)
            destination = sqlite3.connect(temporary)
            try:
                connection.backup(destination)
            finally:
                destination.close()
                connection.close()
            temporary.chmod(target.stat().st_mode & 0o777)
            # The backup is a standalone database. Copied WAL/SHM sidecars belong
            # to the earlier file copy and must never replay into this snapshot.
            for suffix in ("-wal", "-shm"):
                target.with_name(target.name + suffix).unlink(missing_ok=True)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    backup_databases(Path(sys.argv[1]), Path(sys.argv[2]))
