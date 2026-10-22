# 5. One stage engine, versioned pipeline, proof validated by the server

Status: accepted (Phase 3)

## Decision
- Every stage change goes through one function (`pipeline.engine.service.move`) under a row lock on the deal. Ticks, rejections, edits, closing and next steps take the same lock first, so a tick racing a rejection, or two moves, end in one consistent state; the loser gets 409.
- Pipelines are data. A version is `draft`, `published` or `archived`; children (stages, transitions, criteria) of a non-draft version are frozen by a database trigger, and versions are never deleted (the app role cannot DELETE them). Publishing archives the live version and promotes the draft in one transaction.
- A deal copies its stage's checklist when it enters (`deal_stage_entries.checklist_snapshot`). Later publishes never change a deal already in a stage.
- Proof types are a registry of validators (fields, activity, checklist, contact_tag, deal_lines, document) plus `also` for combined proofs. The server validates proof at tick time; a later edit or deletion re-runs the check in the same transaction (`recheck_proofs`) and marks ticks "Proof missing", clearing the mark when proof returns. Time-based conditions (a meeting that had to be in the future) are judged only at tick time.
- Owner-only for tick and forward move, and "not own" for override, come from the permission matrix flags (`own_deals_only`, `not_own_deals`), not from role checks in code.

## Why
The PRD needs configuration without a developer, an audit trail that explains every move, and no way to fake progress. Doing the proof check on the server and in the same transaction as the change means the screen can never show a tick whose proof is already gone.

## Consequences
- Proof-missing is applied synchronously in the transaction that deleted the proof (the prompt suggested outbox listeners); notifications still go through the outbox.
- A line's unit price is frozen to the catalog list price; discount % is the only price lever, so "below the floor" is a discount rule.
- Health (Nudge / Stale / At risk / Slipped) is stored on the deal and refreshed by an event and a nightly job at 00:30 IST, so board filters use an index.
- Stale outranks At risk when both apply; "activity" for staleness means a logged activity (not an edit or a move).
