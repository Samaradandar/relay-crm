# Restore drill

Goal: prove a backup restores into a **fresh** environment, and that the audit trail survives.
Run this once on staging (Phase 1 gate) and then monthly.

## What is backed up
- **Database**: a `pg_dump` custom-format file (`db.dump`). On AWS, RDS also takes daily snapshots
  and keeps point-in-time recovery; the dump is the portable copy.
- **Files**: every object in the private bucket (`S3_BUCKET`), copied with boto3.

## Local / Docker-free drill (verified by `tests/foundation/test_backup_restore.py`)
```
cd api
python scripts/backup_restore.py backup --db "<source postgres url>" --out backups/<date> --no-files
# create an EMPTY database, then:
python scripts/backup_restore.py restore --from backups/<date> --db "<empty database url>" --no-files
```
Then verify (see "Checks" below).

## Docker compose drill (with MinIO files)
```
docker compose exec postgres pg_isready
docker compose run --rm api python scripts/backup_restore.py backup \
    --db postgresql://relay:relay@postgres:5432/relay --out /app/backups/<date>
docker compose exec postgres createdb -U relay relay_restored
docker compose run --rm api python scripts/backup_restore.py restore \
    --from /app/backups/<date> --db postgresql://relay:relay@postgres:5432/relay_restored
```

## AWS drill (do after Phase 0 infrastructure exists)
1. RDS console: restore the latest snapshot **or** point-in-time into a new instance
   (a restore always creates a new database; it never overwrites the old one).
2. Point a throwaway API task at the new endpoint through `DATABASE_URL` and `MIGRATION_DATABASE_URL`.
3. Files: restore the S3 bucket from versioning (or run `backup_restore.py restore` from the copy).
4. Run the checks below. Write down the time taken (target: RTO 1 hour, RPO 15 minutes).
5. Delete the throwaway resources.

## Checks (all must pass)
- `SELECT count(*) FROM users;` equals production.
- Audit chain intact: run the nightly job on the restored database
  `python -c "import asyncio; from app.platform.jobs.app import check_audit_chains as c; print(asyncio.run(c()))"`
  and expect every org `True`.
- Log in as each role and open one record you can see and one you cannot (404).
- `outbox_events` has no stuck `pending` rows older than the backup time.

## After restoring in an emergency
Stop writers and duplicate schedulers first (only one `worker` may run). Start the API, then the
worker. The outbox is at-least-once, so events from just before the failure may be handled twice;
handlers are idempotent (notifications use dedupe keys). Reconcile anything provider-side (emails).
