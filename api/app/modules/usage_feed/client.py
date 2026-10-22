# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP client for the voice platform's usage-api (shared-secret auth, PRD P4-08)."""

from datetime import datetime
from decimal import Decimal

import httpx

from app.core.config import get_settings
from app.modules.usage_feed.service import Page, UsageRow


async def fetch_page(since: str | None, limit: int) -> Page:
    settings = get_settings()
    if not settings.usage_api_url:
        return Page(records=[], next_cursor=since)
    async with httpx.AsyncClient(base_url=settings.usage_api_url, timeout=30) as client:
        resp = await client.get(
            "/usage",
            params={"since": since, "limit": limit} if since else {"limit": limit},
            headers={"X-Usage-Secret": settings.usage_api_secret},
        )
        resp.raise_for_status()
        data = resp.json()
    records = [
        UsageRow(
            source_record_id=r["source_record_id"],
            voice_customer_id=r["voice_customer_id"],
            occurred_at=datetime.fromisoformat(r["occurred_at"]),
            minutes=Decimal(str(r["minutes"])),
            revision=int(r.get("revision", 1)),
        )
        for r in data["records"]
    ]
    return Page(records=records, next_cursor=data.get("next_cursor"))
