# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pydantic request shapes, and the snake_case -> camelCase converter the frontend contract
endpoints (section 12a) need: `web/src/api/index.ts` calls `http<T>()` with no field-mapping
layer of its own, so the JSON this API returns for /me/numbers, /team/overview, /team/reps/{id}
and /admin/overview must use the exact camelCase keys in web/src/api/types.ts. Every other Phase 6
endpoint here (leaderboard, targets, incentives, reports, forecast) is a new contract with no
prior frontend, so it stays snake_case like the rest of this API.

Built in: Phase 6.
"""

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.fields import Opt2000

_WORD_BOUNDARY = re.compile(r"_([a-z0-9])")


def _to_camel(key: str) -> str:
    return _WORD_BOUNDARY.sub(lambda m: m.group(1).upper(), key)


def camelize(value: Any) -> Any:
    if isinstance(value, dict):
        return {_to_camel(k): camelize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [camelize(v) for v in value]
    return value


class TargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_paise: int
    reason: Opt2000 = None


class BulkTargetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    amount_paise: int


class BulkTargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: date
    items: list[BulkTargetItem]
    reason: Opt2000 = None


class IncentiveRateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_bp: int
    reason: Opt2000 = None


class ColourRegenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Opt2000 = None


class RecomputeMonthIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Opt2000 = None
