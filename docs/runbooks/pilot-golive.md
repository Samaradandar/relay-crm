# Pilot and go-live (P3-19)

Blocked on Phase 0 (production on AWS). Do not start the pilot on a laptop.

## Before the pilot
- [ ] Production deployed and restore drill passed (`docs/runbooks/restore.md`).
- [ ] Real users and teams loaded (`api/scripts/load_org.py`), assignment rules and caps set by the sales head.
- [ ] Existing orgs created before the "fit check in Lead" decision: `cd api && python scripts/publish_fit_in_lead.py --all --dry-run`, then without `--dry-run` (publishes a new pipeline version; open deals stay on their old version).
- [ ] Real fit checklist entered (Settings lists), lost reasons reviewed, catalog loaded with list and floor prices.
- [ ] "Slipped" meaning confirmed with Samar (we flag an open deal whose expected close date has passed).
- [ ] Load check on staging: `python scripts/loadtest.py run --url <staging> --users 50 --seconds 60` with 2,000 deals seeded. Targets at p95: board 1.5 s, deal page 1 s, move 300 ms.
- [ ] Error tracking, slow-query log and failed-jobs alerts on.

## Pilot (two weeks, one team)
- The pilot team runs **all** its live deals in the CRM only. The CRM starts empty: no import.
- Weekly bug review. Keep a shared list; each item gets a severity (1 blocks work, 2 slows work, 3 cosmetic). Fix severity 1 the same day.
- Daily 10-minute check-in with the sales head. Look at: failed jobs, notifications that did not arrive, proof-missing warnings that are false alarms, duplicate-check surprises.

## Exit criteria
- [ ] Two weeks with no open severity-1 bug.
- [ ] The five role guides in `docs/guides/` were read by the pilot team and corrected where unclear.
- [ ] Sales head signs off.

## Go-live (all teams)
1. Announce the date; load the remaining users and teams.
2. Turn on the Lead Pool rules for all teams.
3. First week: daily check-in with the sales head, watch error tracking, slow queries and failed jobs.
4. After a week: review win-rate vs probability evidence with the Super admin.
