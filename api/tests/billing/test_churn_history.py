# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P5-07: Churn History list/summary scoping, filters, sort, pagination, rep-breakdown gating,
Returned badge, and export."""

import csv
import io
from datetime import date

import pytest

from app.modules.churn.history import churn_rate
from app.platform.files import storage
from tests.billing.test_churn import _seed_reasons
from tests.billing.test_customer_on_won import win
from tests.billing.test_renewal_cancellation import rows, run
from tests.helpers import headers as H

_CONFIRM_BODY = {
    "billing_frequency": "monthly",
    "term_type": "contract",
    "contract_term": "yearly",
    "start_date": "2026-01-01",
    "contract_end_date": "2026-12-31",
    "pricing_model": "per_minute",
    "pack_price_paise": 150000,
    "pack_size_minutes": "1000",
}


async def _confirmed_customer(client, world, admin_engine, owner=None):
    """Mirrors tests.billing.test_renewal_cancellation._confirmed_customer, but confirms as
    Admin (all-scope) instead of a hardcoded `tl_north`, so a south-team owner works too."""
    did, owner = await win(client, world, admin_engine, owner=owner)
    customer_id = (
        await rows(admin_engine, "SELECT id FROM customers WHERE first_won_deal_id=:d", d=did)
    )[0].id
    sub_id = (
        await rows(admin_engine, "SELECT id FROM subscriptions WHERE customer_id=:c", c=customer_id)
    )[0].id
    r = await client.post(
        f"/subscriptions/{sub_id}/confirm",
        json={**_CONFIRM_BODY, "reason": "confirming"},
        headers=H(world.admin),
    )
    assert r.status_code == 200, r.text
    return owner, customer_id, sub_id


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def presign_put(self, key, content_type):
        return "http://put/" + key

    def presign_get(self, key, filename):
        return "http://get/" + key

    async def size_of(self, key):
        return len(self.objects.get(key, b""))

    async def put_bytes(self, key, data, content_type):
        self.objects[key] = data


@pytest.fixture
def fs():
    s = FakeStorage()
    storage.set_storage(s)
    yield s
    storage.set_storage(None)


async def _churn(
    client,
    world,
    admin_engine,
    *,
    owner=None,
    actor=None,
    reason_code="Price",
    churn_date="2026-06-01",
):
    owner_, customer_id, sub_id = await _confirmed_customer(
        client, world, admin_engine, owner=owner
    )
    r = await client.post(
        f"/customers/{customer_id}/churn",
        json={"churn_date": churn_date, "reason_code": reason_code, "notes": "note"},
        headers=H(actor or world.admin),
    )
    assert r.status_code == 201, r.text
    return r.json(), owner_, customer_id


async def test_churn_history_scoping_user_sees_only_own(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    _, owner1, _ = await _churn(client, world, admin_engine, owner=world.north1)
    _, owner2, _ = await _churn(client, world, admin_engine, owner=world.north2)

    r = await client.get("/churn-history", headers=H(world.north1))
    assert r.status_code == 200, r.text
    body = r.json()
    names = {row["account_owner_name"] for row in body["rows"]}
    assert names == {world.north1.name}


async def test_churn_history_scoping_tl_sees_team(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    await _churn(client, world, admin_engine, owner=world.north1)
    await _churn(client, world, admin_engine, owner=world.north2)
    await _churn(client, world, admin_engine, owner=world.south1)

    r = await client.get("/churn-history", headers=H(world.tl_north))
    assert r.status_code == 200, r.text
    body = r.json()
    names = {row["account_owner_name"] for row in body["rows"]}
    assert names == {world.north1.name, world.north2.name}


async def test_churn_history_scoping_admin_sees_all(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    await _churn(client, world, admin_engine, owner=world.north1)
    await _churn(client, world, admin_engine, owner=world.south1)

    r = await client.get("/churn-history", headers=H(world.admin))
    assert r.status_code == 200, r.text
    body = r.json()
    names = {row["account_owner_name"] for row in body["rows"]}
    assert {world.north1.name, world.south1.name} <= names


async def test_churn_history_filters_by_reason_and_owner(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    await _churn(client, world, admin_engine, owner=world.north1, reason_code="Price")
    await _churn(client, world, admin_engine, owner=world.north2, reason_code="Competitor")

    r = await client.get("/churn-history", params={"reason": "Price"}, headers=H(world.admin))
    assert r.status_code == 200, r.text
    rows_out = r.json()["rows"]
    assert all(row["reason"] == "Price" for row in rows_out)

    r = await client.get(
        "/churn-history", params={"owner_id": str(world.north2.id)}, headers=H(world.admin)
    )
    assert r.status_code == 200, r.text
    rows_out = r.json()["rows"]
    assert all(row["account_owner_name"] == world.north2.name for row in rows_out)


async def test_churn_history_date_range_filter(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    await _churn(client, world, admin_engine, owner=world.north1, churn_date="2026-06-01")
    await _churn(client, world, admin_engine, owner=world.north2, churn_date="2026-07-01")

    r = await client.get(
        "/churn-history",
        params={"date_from": "2026-06-15", "date_to": "2026-07-15"},
        headers=H(world.admin),
    )
    assert r.status_code == 200, r.text
    rows_out = r.json()["rows"]
    assert all(row["churn_date"] == "2026-07-01" for row in rows_out)


async def test_churn_history_sort_by_each_field(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    await _churn(client, world, admin_engine, owner=world.north1, churn_date="2026-06-01")
    await _churn(client, world, admin_engine, owner=world.north2, churn_date="2026-07-01")

    for sort in ("churn_date", "value_lost", "time_as_customer"):
        r = await client.get("/churn-history", params={"sort": sort}, headers=H(world.admin))
        assert r.status_code == 200, (sort, r.text)


async def test_churn_history_pagination_page_sizes(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    for _ in range(3):
        await _churn(client, world, admin_engine, owner=world.north1)

    for size in (10, 25, 50):
        r = await client.get("/churn-history", params={"page_size": size}, headers=H(world.admin))
        assert r.status_code == 200, (size, r.text)
        assert r.json()["page_size"] == size

    r = await client.get("/churn-history", params={"page_size": 7}, headers=H(world.admin))
    assert r.status_code == 422, r.text


async def test_churn_history_returned_badge(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    event, owner, customer_id = await _churn(client, world, admin_engine, owner=world.north1)

    r = await client.get("/churn-history", headers=H(world.admin))
    row = next(x for x in r.json()["rows"] if x["id"] == event["id"])
    assert row["returned"] is False

    any_deal = (await rows(admin_engine, "SELECT id FROM deals LIMIT 1"))[0].id
    await run(
        admin_engine,
        "UPDATE churn_events SET returned_deal_id = :d WHERE id = :i",
        d=any_deal,
        i=event["id"],
    )
    r = await client.get("/churn-history", headers=H(world.admin))
    row = next(x for x in r.json()["rows"] if x["id"] == event["id"])
    assert row["returned"] is True

    r2 = await client.post(
        f"/churn/{event['id']}/reverse", json={"reason": "mistake"}, headers=H(world.sa)
    )
    assert r2.status_code == 200, r2.text
    r = await client.get("/churn-history", headers=H(world.admin))
    row = next(x for x in r.json()["rows"] if x["id"] == event["id"])
    assert row["returned"] is False


async def test_churn_rate_formula_against_hand_built_dataset(client, world, admin_engine):
    """2 churned in [period_start, period_end] / 10 customers active at period start = 0.2."""
    await _seed_reasons(admin_engine, world.org_id)
    org_id = world.org_id
    period_start = date(2026, 6, 1)
    period_end = date(2026, 6, 30)

    # 10 customers already active before the period, won before 2026-06-01
    customer_ids = []
    for _ in range(10):
        _, customer_id, _ = await _confirmed_customer(
            client, world, admin_engine, owner=world.north1
        )
        customer_ids.append(customer_id)
    # 2 of those 10 churn inside the period (the numerator); the other 8 stay active
    for cid, churn_date in zip(customer_ids[:2], ("2026-06-10", "2026-06-20"), strict=True):
        r = await client.post(
            f"/customers/{cid}/churn",
            json={"churn_date": churn_date, "reason_code": "Price", "notes": "note"},
            headers=H(world.admin),
        )
        assert r.status_code == 201, r.text
    await run(
        admin_engine,
        "UPDATE customers SET created_at = '2026-01-01'::timestamptz WHERE org_id = :o",
        o=org_id,
    )

    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(admin_engine) as s:
        rate = await churn_rate(s, org_id, period_start, period_end)
    assert float(rate) == pytest.approx(0.2)


async def test_churn_history_summary_counts_and_top_reasons(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    await _churn(client, world, admin_engine, owner=world.north1, reason_code="Price")
    await _churn(client, world, admin_engine, owner=world.north2, reason_code="Price")
    await _churn(client, world, admin_engine, owner=world.south1, reason_code="Competitor")

    r = await client.get("/churn-history/summary", headers=H(world.admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["churned_all_time"] >= 3
    top = {row["reason"]: row["count"] for row in body["top_reasons"]}
    assert top.get("Price", 0) >= 2


async def test_churn_history_summary_rep_breakdown_gated(client, world, admin_engine):
    await _seed_reasons(admin_engine, world.org_id)
    await _churn(client, world, admin_engine, owner=world.north1)

    r = await client.get(
        "/churn-history/summary", params={"breakdown": "rep"}, headers=H(world.north1)
    )
    assert r.status_code == 200, r.text
    assert not r.json().get("by_rep")

    r = await client.get(
        "/churn-history/summary", params={"breakdown": "rep"}, headers=H(world.tl_north)
    )
    assert r.status_code == 200, r.text
    assert r.json().get("by_rep")

    r = await client.get(
        "/churn-history/summary", params={"breakdown": "rep"}, headers=H(world.admin)
    )
    assert r.status_code == 200, r.text
    assert r.json().get("by_rep")


async def test_churn_history_export_csv_requires_reason_and_logs(client, world, admin_engine, fs):
    await _seed_reasons(admin_engine, world.org_id)
    await _churn(client, world, admin_engine, owner=world.north1)

    r = await client.post("/exports", json={"entity": "churn_history"}, headers=H(world.admin))
    assert r.status_code == 422, r.text  # export.create requires a reason for Admin

    r = await client.post(
        "/exports",
        json={"entity": "churn_history", "reason": "quarterly board review"},
        headers=H(world.admin),
    )
    assert r.status_code == 201, r.text
    export_id = r.json()["id"]

    log = await rows(
        admin_engine, "SELECT entity, reason, row_count FROM export_log WHERE id=:i", i=export_id
    )
    assert log[0].entity == "churn_history"
    assert log[0].reason == "quarterly board review"
    assert log[0].row_count >= 1

    keys = list(fs.objects)
    assert keys
    data = fs.objects[keys[-1]].decode("utf-8-sig")
    out_rows = list(csv.DictReader(io.StringIO(data)))
    assert len(out_rows) >= 1
    assert "Churn date" in out_rows[0]
