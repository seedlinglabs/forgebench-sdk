"""Turn whatever a traced function took or returned into JSON the server accepts.

Never raises: anything that cannot be represented becomes a short ``repr``.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import date, datetime
from enum import Enum
from typing import Any

#: Same cap the server applies to each input / output / metadata field.
MAX_FIELD_CHARS = 64_000
TRUNCATED = "…[truncated]"
_MAX_DEPTH = 20
# Values visited per conversion: a huge or deeply shared structure is cut
# here instead of being walked whole before the size cap applies.
_MAX_NODES = 10_000
_MAX_REPR_CHARS = 1_000
_STRING_PREVIEW = MAX_FIELD_CHARS + 1


def to_jsonable(value: Any) -> Any:
    return _Converter().convert(value, 0)


class _Converter:
    def __init__(self) -> None:
        self.nodes = 0

    @property
    def exhausted(self) -> bool:
        return self.nodes >= _MAX_NODES

    def convert(self, value: Any, depth: int) -> Any:
        self.nodes += 1
        if self.nodes > _MAX_NODES:
            return "<truncated>"
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, str):
            # Longer than the field cap gets cut there anyway; don't copy it whole.
            return value[:_STRING_PREVIEW]
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if depth >= _MAX_DEPTH:
            return "<max depth>"
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if self.exhausted:
                    out["<truncated>"] = True
                    break
                out[str(k)] = self.convert(v, depth + 1)
            return out
        if isinstance(value, (list, tuple, set, frozenset)):
            items = []
            for v in value:
                if self.exhausted:
                    items.append("<truncated>")
                    break
                items.append(self.convert(v, depth + 1))
            return items
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Enum):
            return self.convert(value.value, depth + 1)
        if isinstance(value, (bytes, bytearray)):
            return f"<{len(value)} bytes>"
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return self.convert({f.name: getattr(value, f.name) for f in dataclasses.fields(value)}, depth + 1)
        model_dump = getattr(value, "model_dump", None)  # pydantic v2
        if callable(model_dump):
            try:
                return self.convert(model_dump(mode="json"), depth + 1)
            except Exception:  # noqa: BLE001 - fall through to repr
                pass
        return _safe_repr(value)


def capped(value: Any) -> Any:
    """``value`` when its JSON form fits :data:`MAX_FIELD_CHARS`, else that JSON
    cut to size with a :data:`TRUNCATED` marker — so the server keeps it."""
    if value is None:
        return None
    if isinstance(value, str):
        return value if len(value) <= MAX_FIELD_CHARS else value[:MAX_FIELD_CHARS] + TRUNCATED
    text = json.dumps(value)
    if len(text) <= MAX_FIELD_CHARS:
        return value
    return text[:MAX_FIELD_CHARS] + TRUNCATED


def _safe_repr(value: Any) -> str:
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - a broken __repr__ must not break tracing
        text = f"<{type(value).__name__}>"
    return text[:_MAX_REPR_CHARS]
