"""Tracing: record your agent's own steps as one nested trace.

Mark the functions you want to see; model calls made inside them nest under
them automatically, with tokens and cost::

    from forgebench import Forgebench, observe

    fb = Forgebench()

    @observe(as_type="tool")
    def get_weather(city: str) -> dict:
        return {"city": city, "temp": 18}

    @observe(as_type="agent", name="support-agent")
    def run(question: str) -> str:
        fb.update_current_trace(user_id="u_42", session_id="chat_7")
        weather = get_weather("sf")
        reply = fb.chat.completions.create(model="...", messages=[...])  # nested
        return reply.choices[0].message.content

Steps are sent in the background with the client's API key — no other keys,
no extra setup — and only ever WRITTEN: the SDK never reads traces back.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from ._config import TracingConfig
from ._observation import Level, Observation, ObservationType
from ._tracer import bind_context, make_observe, resolve_tracer

F = TypeVar("F", bound=Callable[..., Any])

_observe = make_observe(resolve_tracer)


def observe(
    fn: Optional[F] = None,
    *,
    name: Optional[str] = None,
    as_type: ObservationType = "span",
    capture_input: bool = True,
    capture_output: bool = True,
) -> Any:
    """Record every call of the decorated function (sync or async) as an
    observation: its arguments as input, its return value as output, its
    duration, and an exception as ``level="ERROR"`` (re-raised unchanged).

    ``name`` defaults to the function's name and can be anything that helps you
    debug. ``as_type`` is one of ``span`` (default), ``agent``, ``tool``,
    ``chain``, ``retriever``, ``embedding``, ``evaluator``, ``guardrail``,
    ``event``. Turn ``capture_input`` / ``capture_output`` off for values that
    are large or sensitive.

    Usable bare (``@observe``) or with arguments (``@observe(as_type="tool")``).
    Inside a trace it records into that trace; otherwise into a new trace of
    the most recently created client. With no client yet, it just calls ``fn``.
    """
    return _observe(
        fn, name=name, as_type=as_type, capture_input=capture_input, capture_output=capture_output
    )


__all__ = [
    "observe",
    "bind_context",
    "Observation",
    "ObservationType",
    "Level",
    "TracingConfig",
]
