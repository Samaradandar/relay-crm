# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test: scratch file (file deletion is blocked in this environment, so it was reused).

It holds the one ordering observation that belongs nowhere else: input validation runs before the
permission check on the merge endpoint."""

import uuid

from tests.deep.phase2.support import configure, create, headers


async def test_merge_validates_input_before_checking_permission(qc, world):
    """NOTE (S4): a Team Leader gets 409 'choose a different account' instead of 403 when merging
    an account into itself, and no denied audit row is written for that call."""
    await configure(qc, world)
    d = await create(qc, world.north1)
    r = await qc.post(
        f"/accounts/{d['account_id']}/merge",
        json={"into_account_id": d["account_id"]},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 409
    r = await qc.post(
        f"/accounts/{d['account_id']}/merge",
        json={"into_account_id": str(uuid.uuid4())},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 403
