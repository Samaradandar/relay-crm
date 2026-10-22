# Deploy

Local (Docker): `make dev` (compose). Docker-free local run: `cd api && python scripts/run_local.py`.

Order on every deploy (identical for staging and production):
1. Build one image (`api/Dockerfile`); the same image runs `api` and `worker`.
2. `alembic upgrade head` with the **migration** role (`MIGRATION_DATABASE_URL`).
3. `python -m app.platform.jobs.schema` (installs/updates the job-queue tables, grants the app role).
4. `python -m app.platform.permissions.sync` if the PRD matrix changed.
5. Roll the API tasks (keep two healthy), then the worker (`python -m app.worker`).
6. Health check: `GET /health`. A failed check must stop the traffic switch.

Schema rules: expand/contract migrations only; never edit a migration that has run in staging.
The running app uses the restricted `relay_app` role: it cannot UPDATE or DELETE `audit_log`.
Production AWS wiring (ECS, RDS, Cognito, SES, S3) is Phase 0 and is configuration only: see `.env.example`.

## After the deploy: smoke test
Run `python scripts/smoke.py --url <env url> --token <smoke-test Admin token>` (read-only, ~10 s).
It checks `/health`, `/health/ready` (the database answers) and the main screens' endpoints with
a time budget each. Exit code non-zero = do not switch traffic; if traffic already moved, roll back.

## Rollback plan
Decide within 15 minutes of the deploy; whoever deployed owns the call. Roll back when the smoke
test fails, `/health/ready` is not 200, the error rate or failed jobs jump, or a severity-1 bug
(blocks work) appears that a quick fix cannot clear the same day.

1. **Code (the usual case).** Redeploy the previous image tag for `api` and `worker` (the same
   image runs both). Because migrations are expand/contract only, the previous code runs against
   the new schema: there is no database step. Keep two healthy API tasks while rolling.
2. **Never run `alembic downgrade` in staging or production.** A migration that must be undone is
   undone by a new forward migration.
3. **A migration that damaged data** (rare, the expand/contract rule exists to prevent it): stop
   the worker, restore the database with RDS point-in-time recovery into a *new* instance from just
   before the deploy (`restore.md`), point the previous image at it, run the restore checks, then
   switch traffic. Note the lost window (target RPO 15 minutes) and tell the sales head.
4. **Pipeline configuration** published by mistake: not a deploy issue. The Super admin rolls the
   pipeline back in Settings > Pipeline (it republishes an earlier version as a new one).
5. **Afterwards:** run the smoke test against the rolled-back version, write down what happened
   and why in the bug list, and fix forward in a new release.

## Monitoring (wire up with the Phase 0 infrastructure)
- **Uptime check** every minute on `GET /health/ready` from outside AWS; alert after 2 failures.
- **Error alerts**: 5xx rate above 1% for 5 minutes on the load balancer; error tracking
  (exceptions with request id) on the API and worker.
- **Failed jobs**: alert when `GET /admin/failed-jobs` is non-empty (the jobs list is the same
  data); the nightly audit-chain check failing is a severity-1 alert.
- **Database**: slow-query log on, CPU/connections/storage alarms on RDS, backups verified by
  the monthly restore drill.
