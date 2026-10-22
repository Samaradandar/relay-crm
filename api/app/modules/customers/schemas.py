# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for customers."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class CustomerOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    account_owner_id: uuid.UUID
    account_owner_name: str | None
    first_won_deal_id: uuid.UUID
    status: str
    voice_platform_customer_id: str | None
    created_at: datetime
