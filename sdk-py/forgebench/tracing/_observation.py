from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

if TYPE_CHECKING:
    from ._tracer import Tracer

ObservationType = Literal[
    "span", "agent", "tool", "chain", "retriever", "embedding", "evaluator", "guardrail", "event"
]
Level = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]

OBSERVATION_TYPES = frozenset(ObservationType.__args__)  # type: ignore[attr-defined]
_UNSET: Any = object()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TraceState:
    """One trace as this process sees it, shared by all of its observations.
    Holds the trace-level fields and whether they still need sending."""

    def __init__(self, trace_id: str, *, sampled: bool, tracer: "Tracer") -> None:
        self.id = trace_id
        self.sampled = sampled
        self.tracer = tracer
        self._fields: Dict[str, Any] = {}
        self._dirty = False
        self._lock = threading.Lock()

    def update(
        self,
        *,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            for key, value in (("name", name), ("user_id", user_id), ("session_id", session_id)):
                if value is not None:
                    self._fields[key] = value
            if tags:
                known = self._fields.setdefault("tags", [])
                known.extend(t for t in tags if t not in known)
            if isinstance(metadata, dict) and metadata:
                self._fields["metadata"] = {**self._fields.get("metadata", {}), **metadata}
            self._dirty = True

    def get(self, key: str) -> Any:
        with self._lock:
            return self._fields.get(key)

    def take_record(self, *, force: bool, defaults: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The trace record to send with an ending observation: when the fields
        changed since the last one, or always for the root (``force``)."""
        with self._lock:
            if not (self._dirty or force):
                return None
            self._dirty = False
            record = {k: v for k, v in defaults.items() if v is not None}
            record.update(self._fields)
            record["id"] = self.id
            return record


class Observation:
    """One step of a trace: the agent, a function, a tool call, a retrieval...

    Created by ``observe``, ``start_as_current_observation`` or
    ``start_observation``. It is sent once, when it ends; until then
    :meth:`update` can change it. ``recording`` is False when tracing is off
    or the trace was sampled out — every call still works, nothing is sent.
    """

    def __init__(
        self,
        *,
        trace: TraceState,
        parent_id: Optional[str],
        as_type: str,
        name: str,
    ) -> None:
        self.id = secrets.token_hex(8)
        self.trace = trace
        self.parent_id = parent_id
        self.type = as_type
        self.name = name
        self.start_time = utc_now()
        self.end_time: Optional[datetime] = None
        self.input: Any = None
        self.output: Any = None
        self.metadata: Dict[str, Any] = {}
        self.level: Level = "DEFAULT"
        self.status_message: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def trace_id(self) -> str:
        return self.trace.id

    @property
    def recording(self) -> bool:
        return self.trace.sampled

    @property
    def ended(self) -> bool:
        return self.end_time is not None

    def update(
        self,
        *,
        name: Optional[str] = None,
        input: Any = _UNSET,
        output: Any = _UNSET,
        metadata: Optional[Dict[str, Any]] = None,
        level: Optional[Level] = None,
        status_message: Optional[str] = None,
    ) -> "Observation":
        """Change this observation before it ends. ``metadata`` is merged into
        what is already there; values are captured (serialised) right away."""
        if not self.recording:
            return self
        prepare = self.trace.tracer.prepare
        with self._lock:
            if name is not None:
                self.name = name
            if input is not _UNSET:
                self.input = prepare(input)
            if output is not _UNSET:
                self.output = prepare(output)
            if metadata:
                prepared = prepare(metadata)
                if isinstance(prepared, dict):
                    self.metadata.update(prepared)
            if level is not None:
                self.level = level
            if status_message is not None:
                self.status_message = self.trace.tracer.prepare_text(status_message)
        return self

    def start_observation(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Observation":
        """A child of this observation that does NOT become the current one —
        for work started and finished in different places. Call ``end()``."""
        return self.trace.tracer.start_observation(
            name, as_type=as_type, input=input, metadata=metadata, parent=self
        )

    def end(self) -> None:
        """Finish and queue this observation for sending. Later calls do nothing."""
        with self._lock:
            if self.end_time is not None:
                return
            self.end_time = utc_now()
        self.trace.tracer.finish(self)

    def to_wire(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "trace_id": self.trace.id,
            "parent_observation_id": self.parent_id,
            "type": self.type,
            "name": self.name,
            "start_time": self.start_time.isoformat(),
            "end_time": (self.end_time or self.start_time).isoformat(),
            "input": self.input,
            "output": self.output,
            "level": self.level,
            "status_message": self.status_message,
            "metadata": self.metadata,
        }
