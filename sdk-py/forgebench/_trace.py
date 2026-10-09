"""Trace correlation helpers.

``trace_id`` GROUPS the governed calls of one logical turn/run: pass the same id
to every ``chat.completions.create(trace_id=...)`` of that turn and they share
one trace in Langfuse and one ``trace_id`` on the ledger
(``client.traces.list(trace_id=...)``). Omit it and the server mints one; read it
back from ``ChatCompletion.trace_id`` or, for a stream, ``stream.trace_id``.
The server also honours a W3C ``traceparent`` header when no ``trace_id`` is
sent. MCP tool calls the model makes are attributed to the model call
automatically (by ``call_id``) — there is nothing to thread into your MCP client.
"""

from __future__ import annotations

import uuid
from typing import Optional

#: Request header that nests a governed call under a previous one. Its value is
#: that call's ``call_id`` (``ChatCompletion.call_id``). Where ``trace_id``
#: GROUPS the calls of one run, this gives them STRUCTURE: the control plane
#: records the new call as a child of the named one, so cost rolls up to the
#: root and the console can draw which call caused which. Correlation only —
#: it never affects whether a call is allowed or what it costs.
PARENT_CALL_HEADER = "X-Parent-Call"
CALL_ID_HEADER = "X-Call-Id"
TRACE_ID_HEADER = "X-Trace-Id"

#: The control plane stores trace ids in a 64-char column and 422s longer ones.
MAX_TRACE_ID_LENGTH = 64


def new_trace_id() -> str:
    """A fresh 32-hex-char id — same format the control plane mints server-side
    (``app.langfuse_client.new_trace_id``) when a caller does not supply one,
    so a trace id looks the same whichever side generated it."""
    return uuid.uuid4().hex


def validate_trace_id(trace_id: Optional[str]) -> Optional[str]:
    """Reject an id the server would 422 — before spending a round trip."""
    if trace_id is None:
        return None
    if not isinstance(trace_id, str) or not 1 <= len(trace_id) <= MAX_TRACE_ID_LENGTH:
        raise ValueError(f"trace_id must be a 1-{MAX_TRACE_ID_LENGTH} character string")
    return trace_id
