"""A single helper for correlating a model call with the tool calls it leads to.

MCP itself carries no notion of "which model call caused this tool call" — the
control plane's tool-call gate accepts a caller-supplied ``trace_id`` for
exactly this reason (see ``app.routers.chat.run_governed_call``'s docstring
and ``docs/MCP-MVP1-PLAN.md``'s G7). Generate ONE id per logical turn and pass
it to ``chat.completions.create(trace_id=...)``; the agent's own MCP client
should thread that same id into every tool call the response leads to, so
they nest under one trace in the trace view instead of each starting an
unrelated one.
"""

from __future__ import annotations

import uuid

#: Request header that nests a governed call under a previous one. Its value is
#: that call's ``call_id`` (``ChatCompletion.call_id``). Where ``trace_id``
#: GROUPS the calls of one run, this gives them STRUCTURE: the control plane
#: records the new call as a child of the named one, so cost rolls up to the
#: root and the console can draw which call caused which. Correlation only —
#: it never affects whether a call is allowed or what it costs.
PARENT_CALL_HEADER = "X-Parent-Call"


def new_trace_id() -> str:
    """A fresh 32-hex-char id — same format the control plane mints server-side
    (``app.langfuse_client.new_trace_id``) when a caller does not supply one,
    so a trace id looks the same whichever side generated it."""
    return uuid.uuid4().hex
