"""Access to the published Dramatis schema."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"
SCHEMA_PATH = SCHEMA_DIR / "dramatis.schema.json"

DOCUMENT_VERSION = "0.1.0"
"""The value written into a document's ``schema_version`` field.

The schema's ``$id`` pins major and minor; this adds the patch level a document records.
A test keeps the two in step, since a document claiming a version the schema does not
recognise is worse than one claiming none.
"""


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """Return the schema document, read once and cached."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_version() -> str:
    """Return the version the schema declares for itself."""
    schema_id = load_schema()["$id"]
    # .../schema/<version>/dramatis.schema.json
    return schema_id.rsplit("/", 2)[-2]
