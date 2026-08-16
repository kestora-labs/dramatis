"""Access to the published Dramatis schema.

The schema document lives beside this module, inside the package, and is read as a package
resource. It used to sit at the repository root and be reached by walking up from
``__file__`` — which resolves in a checkout and nowhere else, because the wheel ships only
the package directory. See **D20**.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

SCHEMA_FILENAME = "dramatis.schema.json"

SCHEMA_RESOURCE: Traversable = files(__package__) / SCHEMA_FILENAME
"""The schema document, addressed as a resource of this package rather than as a path.

A ``Traversable`` and not a ``Path`` on purpose: it is what the installed copy is reached
through, and it stays correct in a build that is not an unpacked directory on disk.
"""

DOCUMENT_VERSION = "0.1.0"
"""The value written into a document's ``schema_version`` field.

The schema's ``$id`` pins major and minor; this adds the patch level a document records.
A test keeps the two in step, since a document claiming a version the schema does not
recognise is worse than one claiming none.
"""


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """Return the schema document, read once and cached."""
    return json.loads(SCHEMA_RESOURCE.read_text(encoding="utf-8"))


def schema_version() -> str:
    """Return the version the schema declares for itself."""
    schema_id = load_schema()["$id"]
    # .../schema/<version>/dramatis.schema.json
    return schema_id.rsplit("/", 2)[-2]
