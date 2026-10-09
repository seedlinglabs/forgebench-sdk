"""Lightweight, dependency-free typed models mirroring the control plane's
``app.schemas`` DTOs.

We deliberately avoid a pydantic dependency in the SDK so it stays tiny and
trivially embeddable. Each model is a frozen dataclass with a ``from_dict``
classmethod that tolerates extra/missing fields (forward-compatible with server
additions). Field names mirror ``app.schemas`` exactly so the JSON maps 1:1.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _filter_known(cls: type, data: Dict[str, Any]) -> Dict[str, Any]:
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass(frozen=True)
class WhoAmI:
    tenant_id: str
    auth_method: str
    identity_id: Optional[str] = None
    email: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WhoAmI":
        return cls(**_filter_known(cls, data))


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Usage":
        return cls(**_filter_known(cls, data or {}))


@dataclass(frozen=True)
class ChatChoiceMessage:
    role: str
    content: Optional[str] = None
    # OpenAI-shaped ``tool_calls`` the model emitted, passed through by the
    # control plane AFTER its response gates ran — every entry here was
    # allowed for this agent. Feed them to ``agent_tools.dispatch`` to execute
    # and report them. None when the model called no tool.
    tool_calls: Optional[List[Dict[str, Any]]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatChoiceMessage":
        return cls(**_filter_known(cls, data or {}))


@dataclass(frozen=True)
class ChatChoice:
    index: int
    message: ChatChoiceMessage
    finish_reason: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatChoice":
        msg = data.get("message") or {}
        return cls(
            index=data.get("index", 0),
            message=ChatChoiceMessage.from_dict(msg),
            finish_reason=data.get("finish_reason"),
        )


@dataclass(frozen=True)
class ChatCompletion:
    id: str
    created: int
    model: str
    choices: List[ChatChoice]
    usage: Usage
    object: str = "chat.completion"
    # The trace_id this call was correlated under: the caller's own value if
    # passed to create(trace_id=...), else the id the server minted. Reuse it
    # on the next governed call of the same turn to group them, or list them
    # with client.traces.list(trace_id=...).
    trace_id: Optional[str] = None
    # This call's id on the control plane's ledger. Pass it as
    # ``parent_call_id`` on the governed calls this one causes (a sub-agent's
    # turn, the next step of a tool loop) and they are recorded as its
    # children — the ledger then holds the run as a tree, not a flat set of
    # rows under one trace. None only on a server older than the lineage
    # feature.
    call_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatCompletion":
        return cls(
            id=data.get("id", ""),
            created=data.get("created", 0),
            model=data.get("model", ""),
            choices=[ChatChoice.from_dict(c) for c in data.get("choices", [])],
            usage=Usage.from_dict(data.get("usage") or {}),
            object=data.get("object", "chat.completion"),
            trace_id=data.get("trace_id"),
            call_id=data.get("call_id"),
        )


@dataclass(frozen=True)
class ChatCompletionChunkDelta:
    role: Optional[str] = None
    content: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatCompletionChunkDelta":
        return cls(role=(data or {}).get("role"), content=(data or {}).get("content"))


@dataclass(frozen=True)
class ChatCompletionChunkChoice:
    index: int
    delta: ChatCompletionChunkDelta
    finish_reason: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatCompletionChunkChoice":
        return cls(
            index=data.get("index", 0),
            delta=ChatCompletionChunkDelta.from_dict(data.get("delta") or {}),
            finish_reason=data.get("finish_reason"),
        )


@dataclass(frozen=True)
class ChatCompletionChunk:
    """One SSE event from a streamed chat completion (OpenAI-shaped)."""

    id: str
    created: int
    model: str
    choices: List[ChatCompletionChunkChoice]
    object: str = "chat.completion.chunk"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatCompletionChunk":
        return cls(
            id=data.get("id", ""),
            created=data.get("created", 0),
            model=data.get("model", ""),
            choices=[
                ChatCompletionChunkChoice.from_dict(c) for c in data.get("choices", [])
            ],
            object=data.get("object", "chat.completion.chunk"),
        )


@dataclass(frozen=True)
class ToolBinding:
    """One tool the calling agent may currently call — an entry from
    ``GET /v1/agent-tools``."""

    server: str
    tool: str
    description: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolBinding":
        return cls(**_filter_known(cls, data or {}))


@dataclass(frozen=True)
class TaskReply:
    """What a ``serve()`` handler returns to finish, pause, or fail a task.
    Build one with :meth:`Task.done`, :meth:`Task.ask`, or :meth:`Task.fail`."""

    state: str  # completed | input_required | failed
    artifacts: Optional[List[Dict[str, Any]]] = None
    message: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "artifacts": self.artifacts,
            "message": self.message,
            "error": self.error,
        }


def message_parts(
    text: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    parts: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """A2A message parts from the convenient forms: ``text`` becomes a text
    part, ``data`` a data part, ``parts`` is passed through. At least one."""
    out: List[Dict[str, Any]] = list(parts or [])
    if text is not None:
        out.insert(0, {"text": text})
    if data is not None:
        out.append({"data": data})
    if not out:
        raise ValueError("a task message needs text, data, or parts")
    return out


@dataclass(frozen=True)
class Task:
    """One task opened on an agent through the door — the A2A task object as
    the control plane records it (``TaskInfo``).

    ``call_id`` is the task's own row on the ledger. A callee serving this
    task passes it as ``parent_call_id`` on every governed call it makes
    while working on it, so those calls hang under the task in the tree.
    """

    id: str
    context_id: str
    state: str
    delivery: str = "pull"
    input: Dict[str, Any] = field(default_factory=dict)
    artifacts: Optional[List[Dict[str, Any]]] = None
    status_message: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    call_id: Optional[str] = None
    parent_call_id: Optional[str] = None
    root_call_id: Optional[str] = None
    caller_agent_id: Optional[str] = None
    callee_agent_id: Optional[str] = None
    trace_id: Optional[str] = None
    created_at: Optional[str] = None
    claimed_at: Optional[str] = None
    completed_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        d = dict(data or {})
        status = d.pop("status", None) or {}
        d["state"] = status.get("state", d.get("state", "submitted"))
        d["status_message"] = status.get("message")
        return cls(**_filter_known(cls, d))

    # --- reading the request -------------------------------------------------
    @property
    def parts(self) -> List[Dict[str, Any]]:
        return list(((self.input or {}).get("message") or {}).get("parts") or [])

    @property
    def text(self) -> str:
        """All text parts of the input message, joined."""
        return "\n".join(p["text"] for p in self.parts if p.get("text") is not None)

    @property
    def data(self) -> Dict[str, Any]:
        """All data parts of the input message, merged (later keys win)."""
        merged: Dict[str, Any] = {}
        for p in self.parts:
            if isinstance(p.get("data"), dict):
                merged.update(p["data"])
        return merged

    @property
    def terminal(self) -> bool:
        return self.state in ("completed", "failed", "canceled")

    @property
    def question(self) -> Optional[str]:
        """The callee's question when ``state == "input_required"``."""
        if not self.status_message:
            return None
        return "\n".join(
            p["text"] for p in self.status_message.get("parts", []) if p.get("text") is not None
        ) or None

    # --- reading the answer --------------------------------------------------
    def artifact(self, name: str) -> Optional[Dict[str, Any]]:
        for a in self.artifacts or []:
            if a.get("name") == name:
                return a
        return None

    def artifact_text(self, name: Optional[str] = None) -> str:
        """Text parts of the named artifact (or of every artifact), joined."""
        arts = [self.artifact(name)] if name else list(self.artifacts or [])
        return "\n".join(
            p["text"]
            for a in arts
            if a
            for p in a.get("parts", [])
            if p.get("text") is not None
        )

    # --- replying, from inside serve() -----------------------------------------
    def done(
        self,
        *,
        text: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        name: str = "response",
    ) -> TaskReply:
        arts = list(artifacts or [])
        if text is not None or data is not None:
            arts.insert(0, {"name": name, "parts": message_parts(text=text, data=data)})
        return TaskReply(state="completed", artifacts=arts)

    def ask(self, question: str) -> TaskReply:
        return TaskReply(
            state="input_required",
            message={"role": "agent", "parts": [{"text": question}]},
        )

    def fail(self, error: str) -> TaskReply:
        return TaskReply(state="failed", error=error[:4000])


@dataclass(frozen=True)
class ToolOutcomeReceipt:
    """Acknowledgement of ``POST /v1/agent-tools/report``: the decision row
    this outcome was recorded on, and the audit row it links to."""

    event_id: str
    audit_id: Optional[str] = None
    reported: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolOutcomeReceipt":
        return cls(**_filter_known(cls, data or {}))


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    model: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Agent":
        return cls(**_filter_known(cls, data))


@dataclass(frozen=True)
class Run:
    id: str
    status: str
    model: str
    agent_id: Optional[str] = None
    input: Dict[str, Any] = field(default_factory=dict)
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Run":
        return cls(**_filter_known(cls, data))

    @property
    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed")


@dataclass(frozen=True)
class MeteringSummary:
    tenant_id: str
    total_events: int
    total_tokens: int
    total_cost_usd: float
    by_model: Dict[str, float]
    monthly_limit_usd: float
    spent_usd: float
    remaining_usd: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MeteringSummary":
        return cls(**_filter_known(cls, data))
