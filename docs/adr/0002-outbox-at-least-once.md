# ADR 0002: The outbox is at-least-once, not exactly-once

Status: accepted (Phase 1).

An event row is written in the same transaction as the change. A worker handles it under
`FOR UPDATE SKIP LOCKED`, retries with backoff, and marks it failed after 10 attempts (listed for
Admins). A crash after a side effect but before the row is marked done means the handler runs
again, so **every handler must be idempotent** (notifications use dedupe keys). We do not claim
exactly-once delivery to email providers; ambiguous sends are accepted as possible duplicates.
