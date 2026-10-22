# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Smoke test to run right after every deploy (staging, then production). Read-only: it changes
nothing. Exit code 0 = the deploy is good; anything else = stop the traffic switch / roll back.

    python scripts/smoke.py --url https://staging.example --token "$SMOKE_TOKEN"
    python scripts/smoke.py --url http://localhost:8000 --dev-email anita@relay.local   # local only

The token is a real sign-in token for a dedicated smoke-test Admin user (staging and production
use Cognito, so there is no password login here). Each check has a time budget; a check that is
slower than its budget fails the smoke test too.
"""

import argparse
import sys
import time

import httpx

# (path, budget in ms, needs a token)
CHECKS: list[tuple[str, int, bool]] = [
    ("/health", 500, False),
    ("/health/ready", 1000, False),
    ("/me", 1000, True),
    ("/deals", 2000, True),
    ("/board", 1500, True),
    ("/today", 1500, True),
    ("/pool", 1500, True),
    ("/stages/lead/deals", 1500, True),
    ("/approvals", 1000, True),
    ("/notifications", 1000, True),
    ("/admin/failed-jobs", 1000, True),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", help="Bearer token of the smoke-test Admin user")
    ap.add_argument("--dev-email", help="local only: sign in through /auth/dev/login")
    args = ap.parse_args()
    failed = 0
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=15) as c:
        token = args.token
        if not token and args.dev_email:
            r = c.post("/auth/dev/login", json={"email": args.dev_email})
            r.raise_for_status()
            token = r.json()["token"]
        h = {"Authorization": f"Bearer {token}"} if token else {}
        for path, budget, auth in CHECKS:
            if auth and not token:
                print(f"SKIP {path} (no token)")
                continue
            t0 = time.perf_counter()
            try:
                r = c.get(path, headers=h if auth else {})
                status = r.status_code
            except httpx.HTTPError as exc:
                status, r = 0, None
                print(f"FAIL {path}: {exc}")
            ms = (time.perf_counter() - t0) * 1000
            ok = status == 200 and ms <= budget
            failed += not ok
            mark = "OK  " if ok else "FAIL"
            print(f"{mark} {path:<22} {status:>3} {ms:>6.0f} ms (budget {budget})")
        if token:  # a failed background job right after a deploy is worth a look
            jobs = c.get("/admin/failed-jobs", headers=h)
            if jobs.status_code == 200 and jobs.json():
                n = len(jobs.json())
                print(f"WARN {n} failed background job(s): check Admin > Failed jobs")
    print("SMOKE TEST", "PASSED" if not failed else f"FAILED ({failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
