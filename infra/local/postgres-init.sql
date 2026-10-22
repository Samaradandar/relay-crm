-- Local Postgres bootstrap. Runs once on first start.
-- `relay` (POSTGRES_USER) owns the schema and runs migrations.
-- `relay_app` is the restricted runtime role: Alembic migrations grant it
-- table rights, and audit_log gets INSERT/SELECT only.
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE ROLE relay_app LOGIN PASSWORD 'relay_app';
GRANT CONNECT ON DATABASE relay TO relay_app;
GRANT USAGE ON SCHEMA public TO relay_app;
ALTER DEFAULT PRIVILEGES FOR ROLE relay IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO relay_app;
ALTER DEFAULT PRIVILEGES FOR ROLE relay IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO relay_app;
CREATE DATABASE relay_test OWNER relay;
