# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Backup and restore: a database dump plus a copy of the private file bucket.

    python scripts/backup_restore.py backup  --db <postgres url> --out backups/2026-10-07
    python scripts/backup_restore.py restore --from backups/2026-10-07 --db <empty database url>

The target database for restore must already exist and be empty. Files are copied with boto3,
so the same script works against MinIO locally and S3 on AWS (set S3_* in the environment).
See docs/runbooks/restore.md for the full drill.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def pg_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    try:  # the embedded PostgreSQL that ships with pgserver (used for Docker-free local runs)
        import pgserver

        bundled = (
            Path(pgserver.__file__).parent
            / "pginstall"
            / "bin"
            / (name + ".exe" if os.name == "nt" else name)
        )
        if bundled.exists():
            return str(bundled)
    except ImportError:
        pass
    raise SystemExit(f"{name} not found: install the PostgreSQL client tools or add them to PATH")


def _s3():  # type: ignore[no-untyped-def]
    import boto3

    kwargs = {"region_name": os.environ.get("S3_REGION", "ap-south-1")}
    if os.environ.get("S3_ENDPOINT_URL"):
        kwargs["endpoint_url"] = os.environ["S3_ENDPOINT_URL"]
    if os.environ.get("S3_ACCESS_KEY"):
        kwargs["aws_access_key_id"] = os.environ["S3_ACCESS_KEY"]
        kwargs["aws_secret_access_key"] = os.environ["S3_SECRET_KEY"]
    return boto3.client("s3", **kwargs)


def backup(db: str, out: Path, bucket: str | None) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    dump = out / "db.dump"
    subprocess.run(
        [pg_tool("pg_dump"), "--format=custom", "--no-owner", f"--file={dump}", db], check=True
    )
    files = 0
    if bucket:
        client = _s3()
        target = out / "files"
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                dest = target / obj["Key"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                client.download_file(bucket, obj["Key"], str(dest))
                files += 1
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "dump": dump.name,
        "dump_bytes": dump.stat().st_size,
        "files": files,
        "bucket": bucket,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def restore(src: Path, db: str, bucket: str | None) -> dict[str, object]:
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    subprocess.run(
        [
            pg_tool("pg_restore"),
            "--no-owner",
            "--exit-on-error",
            f"--dbname={db}",
            str(src / manifest["dump"]),
        ],
        check=True,
    )
    restored = 0
    files_dir = src / "files"
    if bucket and files_dir.exists():
        client = _s3()
        for path in files_dir.rglob("*"):
            if path.is_file():
                client.upload_file(str(path), bucket, path.relative_to(files_dir).as_posix())
                restored += 1
    return {"database": "restored", "files_restored": restored}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--db", required=True)
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--bucket", default=os.environ.get("S3_BUCKET"))
    b.add_argument("--no-files", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("--from", dest="src", required=True, type=Path)
    r.add_argument("--db", required=True)
    r.add_argument("--bucket", default=os.environ.get("S3_BUCKET"))
    r.add_argument("--no-files", action="store_true")
    args = ap.parse_args()
    bucket = None if args.no_files else args.bucket
    result = (
        backup(args.db, args.out, bucket)
        if args.cmd == "backup"
        else restore(args.src, args.db, bucket)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())
