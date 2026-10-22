# ADR 0003: Audit log integrity

Status: accepted (Phase 1); external anchoring is a follow-up.

`audit_log` is append-only: the app role has INSERT/SELECT only, and a trigger blocks UPDATE,
DELETE and TRUNCATE for everyone including the table owner. Each row stores the previous row's
hash; rows are appended one at a time per org under an advisory lock so the chain cannot fork.
A nightly job re-computes the chain and alerts Super admins when it breaks.

Limit: a database superuser can disable the trigger and rewrite the chain consistently. To detect
that, a later step should copy a signed digest of the latest hash to storage outside the database
(for example an S3 object with Object Lock) after each nightly check. Not built yet.
