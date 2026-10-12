"""Trace correlation helpers.

``trace_id`` GROUPS the governed calls of one logical turn/run: pass the same id
to every ``chat.completions.create(trace_id=...)`` of that turn and they share
one trace. Omit it and the server mints one; read it back from
``ChatCompletion.trace_id`` or, for a stream, ``stream.trace_id``. Inside an
observed step (:mod:`forgebench.tracing`) the SDK fills it in for you. The
server also honours a W3C ``traceparent`` header when no ``trace_id`` is sent.
MCP tool calls the model makes are attributed to the model call automatically
(by ``call_id``) — there is nothing to thread into your MCP client.
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

#: Request header naming the observation (an SDK tracing step) a governed call
#: was made from; the server nests the call's generation under it. Set by the
#: SDK itself inside ``observe`` / ``start_as_current_observation``.
PARENT_OBSERVATION_HEADER = "X-Parent-Observation"

#: The control plane stores trace ids in a 64-char column and 422s longer ones.
MAX_TRACE_ID_LENGTH = 64

#: Voice Conversation key for calls that do not carry ``metadata.session``.
SESSION_HEADER = "X-Forgebench-Session"

#: Explicit conversation turn (with SESSION_HEADER): one Langfuse trace per turn.
TURN_HEADER = "X-Forgebench-Turn"

#: The Voice Conversation key is bounded server-side too.
MAX_SESSION_LENGTH = 128

#: Trace-id response header of the /v1/audio/* routes (chat uses TRACE_ID_HEADER).
AUDIO_TRACE_ID_HEADER = "X-Forgebench-Trace-Id"


def new_trace_id() -> str:
    """A fresh 32-hex-char id — same format the control plane mints server-side
    when a caller does not supply one, so a trace id looks the same whichever
    side generated it."""
    return uuid.uuid4().hex


def validate_trace_id(trace_id: Optional[str]) -> Optional[str]:
    """Reject an id the server would 422 — before spending a round trip."""
    if trace_id is None:
        return None
    if not isinstance(trace_id, str) or not 1 <= len(trace_id) <= MAX_TRACE_ID_LENGTH:
        raise ValueError(f"trace_id must be a 1-{MAX_TRACE_ID_LENGTH} character string")
    return trace_id


def _current_traceparent() -> Optional[str]:
    """W3C ``traceparent`` of the active OpenTelemetry span, or None. OpenTelemetry is
    NOT a dependency: without it (or without a recording span) this is None."""
    try:
        from opentelemetry import trace as otel_trace  # type: ignore[import-not-found]

        ctx = otel_trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return None
        return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags) & 0xFF:02x}"
    except Exception:  # noqa: BLE001 - optional integration, never an error
        return None


def voice_headers(
    session: str, turn: Optional[int] = None, traceparent: Optional[str] = None
) -> dict[str, str]:
    """Headers that tie a voice agent's /v1/audio/* and chat calls to one conversation.

    ``X-Forgebench-Session`` always; ``X-Forgebench-Turn`` when ``turn`` is given (one
    trace per turn without OpenTelemetry); ``traceparent`` when given, else taken from
    the current OpenTelemetry span when ``opentelemetry`` is importable (best accuracy:
    your pipeline's spans and ours join in one trace). Do NOT put a phone number or
    other personal data in ``session``: it is stored and shown."""
    if not isinstance(session, str) or not 1 <= len(session) <= MAX_SESSION_LENGTH or not session.isascii() or not session.isprintable() or session != session.strip():
        raise ValueError(f"session must be 1-{MAX_SESSION_LENGTH} printable ASCII characters")
    headers = {SESSION_HEADER: session}
    if turn is not None:
        if isinstance(turn, bool) or not isinstance(turn, int) or turn < 0:
            raise ValueError("turn must be a non-negative integer")
        headers[TURN_HEADER] = str(turn)
    tp = traceparent if traceparent is not None else _current_traceparent()
    if tp:
        headers["traceparent"] = tp
    return headers
