# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Writes the OpenAPI schema to a file (default web/openapi.json) for type generation."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

out = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parents[2] / "web" / "openapi.json"
)
out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
print(f"wrote {out}")
