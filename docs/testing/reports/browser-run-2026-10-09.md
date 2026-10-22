# Browser test run, 2026-10-09

Stacks (own ports, fresh data, demo data, fake S3 on 9111): phase 1 API 8111 / web 5211 (`br1`), phase 2 8112 / 5212 (`br2`), phase 3 8113 / 5213 (`br3`, later a fresh `br4` on 8114 / 5214). All stopped afterwards; the manual stack (8010 / 5180) was not touched.
Set `E2E_S3_PORT=9111` for the phase 3 export spec (default 9103).

## Counts

| Phase | Before (first run) | After |
|---|---|---|
| Phase 1 | 66 passed | 66 passed |
| Phase 2 | 54 passed | 54 passed |
| Phase 3 | 52 passed, 6 failed | 59 passed (58 specs + 1 new; 2 of them are expected failures: BUG-P3-30, BUG-BR-02), 0 failed |

No top-level `web/e2e/*.spec.ts` exist. Unit tests: vitest 74 passed (19 files). `tsc -b --noEmit` clean. `eslint src` clean.

## Failures and triage

| Spec | Class | Reason / action |
|---|---|---|
| exports: rep exports open deals | ENVIRONMENT | Hard-coded fake S3 port 9103. Now `E2E_S3_PORT` (default 9103). |
| profiles: TL monitoring | TEST | Demo data has a third North rep (Aarav Kapoor). Expectation updated. |
| responsive: BUG-P3-30 catalog scrolls sideways at 400 px | PRODUCT BUG | Reproduced (overflow 357 px). `test.fail()` added (the old mark had been removed). |
| responsive: keyboard on deal page | TEST | Escape now closes the Edit dialog, so the following Cancel click had nothing to click. Now asserts the dialog is closed. |
| walkthrough: BUG-P3-32 | TEST | Regex `/^Move to /` also matched "Move to another team" (the transfer request, a different action). Regex excludes it; the assertion on Move to <stage> is unchanged. |
| walkthrough: Lead to Won (Verify, Tick, Move not shown; flaky, 1 of 4 passed) | PRODUCT BUG (BUG-BR-02), worked around in the harness | See below. |
| walkthrough saveEdit | TEST | Dialog sometimes closes later than 3 s (app busy refetching). Helper now waits for either close or the "reloaded" notice. |

## Product bugs (not patched)

- **BUG-BR-02 (S3)**: on connect `web/src/api/live.ts` replays `/events?since=0` and calls `invalidateFor` once per event, so every page load fires about 200 refetches of notifications (and, for a Team Leader on a deal page, about 200 refetches of the deal, activities, documents and lines, lasting over 10 s). During that burst a click on a button can appear to do nothing: the server accepted the action (204) but the screen stays stale (Verify stayed "Pending", Tick and Move not shown) until reload. Worse as the event table grows. Fix: do not replay when `lastId` is 0 (or only fetch the latest id), and invalidate once per batch. Test: `walkthrough.spec.ts` "BUG-BR-02" (`test.fail`, counts GET /notifications after load, expects at most 10). Harness: phase 3 `signIn` and the `rep.reload()` calls in the walkthrough wait for network idle so the long walkthrough is stable (4 of 4 passes after that).
- **BUG-P3-30 (S4)**: catalog page scrolls sideways at 400 px.

## Notes

- Specs `phase3/walkthrough.spec.ts`, `exports.spec.ts`, `profiles.spec.ts`, `responsive.spec.ts`, `support.ts` were edited. No product code was changed.
- Phase 3 specs for exports and the real file upload ran for real against the fake S3 and passed.
