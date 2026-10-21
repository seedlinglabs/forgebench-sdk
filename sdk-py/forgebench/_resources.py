"""Synchronous API resource groups: chat, agents, runs.

These map 1:1 onto the control plane's governed endpoints:

  chat.completions.create  -> POST /v1/chat/completions  (sync + SSE stream)
  agents.list              -> GET  /v1/agents
  agents.create            -> POST /v1/agents
  agents.register          -> POST /v1/agents/register
  workflows.*              -> /v1/workflows  (list, get, checks, register, ...)
  client.workflow(slug, keys=...).run()  -> chat calls per step, X-Parent-Call threaded
  agents.run               -> POST /v1/runs  (with agent_id)  + optional wait
  runs.create              -> POST /v1/runs
  runs.get                 -> GET  /v1/runs/{id}
  runs.wait                -> poll GET /v1/runs/{id} until terminal

Every call rides the api-key (``sk_...``) auth path, which is the permanent
programmatic surface of Forgebench. The chat path is the governed chokepoint:
the server authenticates, isolates the tenant via RLS, gates the budget BEFORE
any provider call (a 402 surfaces here as :class:`BudgetExceededError`), calls
the model gateway, then meters and audits — all in one tenant transaction.
"""

from __future__ import annotations

import json
import os
import logging
import time
from contextlib import AbstractContextManager
from typing import Literal, overload, Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple, Union

import httpx

from ._trace import (
    AUDIO_TRACE_ID_HEADER,
    CALL_ID_HEADER,
    PARENT_CALL_HEADER,
    PARENT_OBSERVATION_HEADER,
    SESSION_HEADER,
    TRACE_ID_HEADER,
    validate_trace_id,
)
from ._transport import SyncTransport, seg
from .tracing._tracer import Tracer, current_observation
from .types import (
    Agent,
    ChatCompletion,
    ChatCompletionChunk,
    MeteringSummary,
    Run,
    SpeechAudio,
    Task,
    TaskReply,
    ToolBinding,
    ToolOutcomeReceipt,
    Transcription,
    WhoAmI,
    message_parts,
)

MessageLike = Union[Dict[str, Any], "object"]

# The message fields the chokepoint's ChatMessage model accepts. A tool loop
# needs all of them: an assistant turn that carries ``tool_calls`` (and often a
# None ``content``), and ``role: tool`` replies keyed by ``tool_call_id``.
# Flattening every message to role + str(content) — what this did before —
# silently dropped the ids and sent the literal string "None", which every
# provider rejects with "tool messages must include a non-empty tool_call_id".
_MESSAGE_FIELDS = ("role", "content", "tool_calls", "tool_call_id", "name")


def _normalize_messages(messages: List[MessageLike]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        get = m.get if isinstance(m, dict) else (lambda k, _m=m: getattr(_m, k, None))
        msg: Dict[str, Any] = {"role": str(get("role"))}
        content = get("content")
        # None stays None (an assistant tool-call turn); anything else that is
        # not already a content-parts list is sent as text.
        if content is not None:
            msg["content"] = content if isinstance(content, (str, list)) else str(content)
        else:
            msg["content"] = None
        for key in _MESSAGE_FIELDS[2:]:
            value = get(key)
            if value is not None:
                msg[key] = value
        out.append(msg)
    return out


logger = logging.getLogger("forgebench")


class _Unset:
    """Marks an argument the caller did not pass (``None`` means something: unrestricted)."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<unset>"


_UNSET: Any = _Unset()


def _chat_payload(
    *,
    model: str,
    messages: List[MessageLike],
    stream: bool,
    temperature: Optional[float],
    max_tokens: Optional[int],
    trace_id: Optional[str],
    extra: Optional[Mapping[str, Any]],
    metadata: Optional[Mapping[str, Any]] = None,
    session_id: Optional[str] = None,
    user: Optional[str] = None,
    tags: Optional[List[str]] = None,
    dimensions: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": _normalize_messages(messages),
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if trace_id is not None:
        # Stamped on the audit/metering rows and the trace; reuse it
        # across the calls of one turn to group them (see forgebench._trace).
        payload["trace_id"] = validate_trace_id(trace_id)
    if metadata or dimensions:
        meta = dict(metadata or {})
        if dimensions:
            meta["dimensions"] = {**dict(meta.get("dimensions") or {}), **dict(dimensions)}
        payload["metadata"] = meta
    if session_id is not None:
        payload["session_id"] = session_id
    if user is not None:
        payload["user"] = user
    if tags:
        payload["tags"] = list(tags)
    if extra:
        # NOTE: the chokepoint uses extra='ignore' and strips api_key/api_base,
        # so these never leak to the provider; passthrough is safe + harmless.
        for k, v in extra.items():
            payload.setdefault(k, v)
    return payload


def _in_current_trace(
    tracer: Optional["Tracer"], trace_id: Optional[str], user: Optional[str], session_id: Optional[str]
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """A governed call made inside an observed step joins that step's trace, as
    its child: ``(trace_id, user, session_id, parent_observation_id)``, with the
    trace's own user / session as defaults. Only a step of THIS client's tracer
    counts — another client's trace (another workspace) never lends its ids or
    user. An explicit ``trace_id`` of another trace wins, and the call is then
    not nested."""
    observation = current_observation()
    if observation is None or not observation.recording or observation.trace.tracer is not tracer:
        return trace_id, user, session_id, None
    if trace_id is not None and trace_id != observation.trace_id:
        return trace_id, user, session_id, None
    trace = observation.trace
    return (
        observation.trace_id,
        user if user is not None else trace.get("user_id"),
        session_id if session_id is not None else trace.get("session_id"),
        observation.id,
    )


def _is_meta_chunk(event: Dict[str, Any]) -> bool:
    """The server's terminal correlation chunk: empty choices + both ids."""
    return not event.get("choices") and "trace_id" in event and "call_id" in event


class _StreamIds:
    trace_id: Optional[str] = None
    call_id: Optional[str] = None

    def _on_response(self, response: httpx.Response) -> None:
        self.trace_id = response.headers.get(TRACE_ID_HEADER) or self.trace_id
        self.call_id = response.headers.get(CALL_ID_HEADER) or self.call_id

    def _take_meta(self, event: Dict[str, Any]) -> bool:
        if not _is_meta_chunk(event):
            return False
        self.trace_id = event.get("trace_id") or self.trace_id
        self.call_id = event.get("call_id") or self.call_id
        return True


class ChatStream(_StreamIds):
    """Iterator of :class:`ChatCompletionChunk` for a streamed call.

    ``trace_id`` / ``call_id`` are set once the response headers arrive (on the
    first ``next()``), and confirmed by the server's terminal correlation chunk,
    which is consumed here rather than yielded."""

    def __init__(self, transport: SyncTransport, payload: Dict[str, Any], headers: Mapping[str, Any]) -> None:
        self._events = transport.stream_sse(
            "POST", "/v1/chat/completions", json_body=payload, headers=headers,
            on_response=self._on_response,
        )

    def __iter__(self) -> "ChatStream":
        return self

    def __next__(self) -> ChatCompletionChunk:
        while True:
            event = next(self._events)
            if not self._take_meta(event):
                return ChatCompletionChunk.from_dict(event)

    def close(self) -> None:
        self._events.close()


class Completions:
    def __init__(self, transport: SyncTransport, tracer: Optional["Tracer"] = None) -> None:
        self._t = transport
        self._tracer = tracer

    def create(
        self,
        *,
        messages: List[MessageLike],
        model: str = "mock-gpt",
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        trace_id: Optional[str] = None,
        parent_call_id: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        session_id: Optional[str] = None,
        user: Optional[str] = None,
        tags: Optional[List[str]] = None,
        dimensions: Optional[Mapping[str, str]] = None,
    ) -> Union[ChatCompletion, ChatStream]:
        """Create a chat completion through the governed chokepoint.

        With ``stream=False`` returns a :class:`ChatCompletion`. With
        ``stream=True`` returns a :class:`ChatStream` — an iterator of
        :class:`ChatCompletionChunk` (OpenAI-shaped SSE deltas) that also
        carries ``trace_id`` and ``call_id``.

        ``trace_id`` (1-64 chars): reuse the SAME value across the governed
        calls of one turn to group them under one trace. Generate one with
        :func:`forgebench.new_trace_id`; omit for the server to mint one (read
        it back from the response / stream).

        Trace attribution (never sent to the model provider): ``user`` — your
        end-user id, becomes the trace's user; ``session_id`` — groups
        traces into a session; ``tags`` — extra trace tags (up to
        10; ``tenant:``/``agent:``/``key:``/``run``/``tool:`` prefixes are
        reserved and dropped); ``dimensions`` — ``{key: value}`` filter
        dimensions (merged into ``metadata["dimensions"]``); ``metadata`` —
        free-form trace metadata.

        ``parent_call_id``: the ``call_id`` of the governed call that CAUSED
        this one — the orchestrating turn whose answer led here, or the
        previous turn of a tool loop. The server records this call as its
        child, so the ledger holds the workflow as a tree (cost rolls up to
        the root, the console draws who called what) instead of a flat set
        of rows sharing a trace. Where ``trace_id`` groups, this structures.
        Read it off the previous response's :attr:`ChatCompletion.call_id`.
        Optional — omit and this call is a root. Never affects whether the
        call is allowed or what it costs.

        Inside an observed step (:func:`forgebench.observe`) the call joins
        that step's trace as its child — ``trace_id`` and the trace's user /
        session are filled in for you.
        """
        trace_id, user, session_id, parent_observation = _in_current_trace(
            self._tracer, trace_id, user, session_id
        )
        payload = _chat_payload(
            model=model,
            messages=messages,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
            trace_id=trace_id,
            extra=extra_body,
            metadata=metadata,
            session_id=session_id,
            user=user,
            tags=tags,
            dimensions=dimensions,
        )
        headers = {PARENT_CALL_HEADER: parent_call_id, PARENT_OBSERVATION_HEADER: parent_observation}
        if stream:
            return ChatStream(self._t, payload, headers)
        data = self._t.request(
            "POST", "/v1/chat/completions", json_body=payload, headers=headers
        )
        return ChatCompletion.from_dict(data)


class Chat:
    def __init__(self, transport: SyncTransport, tracer: Optional["Tracer"] = None) -> None:
        self.completions = Completions(transport, tracer)


class Agents:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def list(self) -> List[Agent]:
        data = self._t.request("GET", "/v1/agents")
        items = data if isinstance(data, list) else (data or {}).get("data", [])
        return [Agent.from_dict(a) for a in items]

    def create(
        self,
        *,
        name: str,
        owner_identity_id: str,
        model: str = "mock-gpt",
        description: Optional[str] = None,
        system_prompt: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Agent:
        """Create an agent.

        ``owner_identity_id`` — the accountable person for this agent (F1) —
        is REQUIRED as of the MCP tool-governance release: the control plane
        rejects an agent with no named owner. Resolve it from
        ``client.account.whoami()`` (or an operator picker in your app) rather
        than hardcoding a value.
        """
        body = {
            "name": name,
            "owner_identity_id": owner_identity_id,
            "model": model,
            "description": description,
            "system_prompt": system_prompt,
            "config": config or {},
        }
        if tags:
            body["tags"] = tags
        data = self._t.request("POST", "/v1/agents", json_body=body)
        return Agent.from_dict(data)

    def register(
        self,
        *,
        name: str,
        owner_identity_id: str,
        model: str = "mock-gpt",
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        monthly_budget_usd: Optional[float] = None,
        rpm_limit: Optional[int] = None,
        daily_limit_usd: Optional[float] = None,
        allowed_models: Any = _UNSET,
        issue_credential: bool = True,
    ) -> Dict[str, Any]:
        """Register an agent and its credential: ``POST /v1/agents/register``.

        Returns ``{"agent": ..., "credential": {"api_key": ...} | None, ...}``.
        The raw ``api_key`` is shown ONCE, here. ``issue_credential=False``
        registers the agent SEALED (``credential`` is ``None``); its owner then
        reveals a key once in the console. ``allowed_models`` omitted = the
        workspace default, ``None`` = an unrestricted key, a list = that list.
        """
        return self._t.request("POST", "/v1/agents/register", json_body=_register_body(
            name=name, owner_identity_id=owner_identity_id, model=model, description=description,
            tags=tags, system_prompt=system_prompt, config=config, monthly_budget_usd=monthly_budget_usd,
            rpm_limit=rpm_limit, daily_limit_usd=daily_limit_usd, allowed_models=allowed_models,
            issue_credential=issue_credential,
        ))

    @overload
    def run(self, agent_id: str, *, message: str, stream: Literal[True],
        input: Optional[Dict[str, Any]] = ...,
        model: Optional[str] = ...,
        wait: bool = ...,
        timeout: Optional[float] = ...,
        poll_interval: float = ...,
        top_k: Optional[int] = ...,
        dimensions: Optional[Mapping[str, str]] = ...,
        session: Optional[str] = ...,
    ) -> Iterator[Dict[str, Any]]: ...

    @overload
    def run(self, agent_id: str, *, message: str, stream: Literal[False] = ...,
        input: Optional[Dict[str, Any]] = ...,
        model: Optional[str] = ...,
        wait: bool = ...,
        timeout: Optional[float] = ...,
        poll_interval: float = ...,
        top_k: Optional[int] = ...,
        dimensions: Optional[Mapping[str, str]] = ...,
        session: Optional[str] = ...,
    ) -> Dict[str, Any]: ...

    @overload
    def run(self, agent_id: str, *, message: None = ..., stream: bool = ...,
        input: Optional[Dict[str, Any]] = ...,
        model: Optional[str] = ...,
        wait: bool = ...,
        timeout: Optional[float] = ...,
        poll_interval: float = ...,
        top_k: Optional[int] = ...,
        dimensions: Optional[Mapping[str, str]] = ...,
        session: Optional[str] = ...,
    ) -> Run: ...

    def run(
        self,
        agent_id: str,
        *,
        input: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        wait: bool = False,
        timeout: Optional[float] = None,
        poll_interval: float = 0.5,
        message: Optional[str] = None,
        stream: bool = False,
        top_k: Optional[int] = None,
        dimensions: Optional[Mapping[str, str]] = None,
        session: Optional[str] = None,
    ) -> Union[Run, Dict[str, Any], Iterator[Dict[str, Any]]]:
        """Run an agent.

        Two modes. With ``message`` the agent answers once through the
        governed, grounded endpoint (``POST /v1/agents/{id}/run``): the cited
        answer comes back as a dict, or with ``stream=True`` an iterator of
        the server's SSE events (dicts, in order) -- the same machinery as
        chat streaming, so a budget 402 raises before the stream opens.
        ``session`` sets ``X-Forgebench-Session`` so the agent's calls group
        into one Voice Conversation.

        Without ``message`` the legacy asynchronous path is used
        (``POST /v1/runs`` with an ``agent_id``), which routes back through
        the same chokepoint (budget re-gated per model hop, metered,
        audited). With ``wait=True`` the call blocks, polling until the run
        reaches a terminal state. There is no polling deadline unless ``timeout``
        is supplied; expiry stops waiting without cancelling the run.
        """
        if message is not None:
            body = _agent_message_body(message, stream, top_k, dimensions)
            path = f"/v1/agents/{seg(agent_id)}/run"
            headers = {SESSION_HEADER: session}
            if stream:
                return self._t.stream_sse(
                    "POST", path, json_body=body, headers=headers, require_terminal=True
                )
            return self._t.request("POST", path, json_body=body, headers=headers)
        run = self._runs_create(agent_id=agent_id, input=input, model=model)
        if wait and not run.is_terminal:
            return _wait_run(self._t, run.id, timeout=timeout, poll_interval=poll_interval)
        return run

    def _runs_create(
        self,
        *,
        agent_id: Optional[str],
        input: Optional[Dict[str, Any]],
        model: Optional[str],
    ) -> Run:
        body: Dict[str, Any] = {"input": input or {}}
        if agent_id is not None:
            body["agent_id"] = agent_id
        if model is not None:
            body["model"] = model
        data = self._t.request("POST", "/v1/runs", json_body=body)
        return Run.from_dict(data)

    # --- Agent -> agent through the door (A2A tasks) ---------------------------
    def call(
        self,
        callee: str,
        *,
        text: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        parts: Optional[List[Dict[str, Any]]] = None,
        context_id: Optional[str] = None,
        wait: float = 30.0,
        parent_call_id: Optional[str] = None,
    ) -> Task:
        """Open a task on another agent, through the door (A2A ``SendMessage``).

        Requires an AGENT credential bound to ``callee`` (an agent id or
        name) by an operator — the control plane refuses otherwise, with one
        collapsed ``not_permitted`` whatever the reason. The callee runs
        wherever it runs: the door relays to its endpoint, or the callee's
        own ``serve()`` loop claims the task. ``wait`` blocks up to that many
        seconds for an answer (``completed``, ``failed``, or a question —
        ``input_required``); a task still ``working`` after that is returned
        as-is and can be polled with :meth:`task`.

        ``parent_call_id`` nests this task under the governed call that led
        to it, so the callee's whole subtree rolls up under your workflow.
        Answer a question by calling again with the task's ``context_id``.
        """
        body: Dict[str, Any] = {
            "message": {"role": "user", "parts": message_parts(text=text, data=data, parts=parts)},
        }
        if context_id:
            body["context_id"] = context_id
        resp = self._t.request(
            "POST",
            f"/v1/agents/{seg(callee)}/tasks",
            json_body=body,
            params={"wait": wait},
            headers={PARENT_CALL_HEADER: parent_call_id},
        )
        return Task.from_dict(resp)

    def task(self, callee: str, task_id: str, *, wait: float = 0.0) -> Task:
        """Read a task you opened or were asked to do (A2A ``GetTask``)."""
        resp = self._t.request("GET", f"/v1/agents/{seg(callee)}/tasks/{seg(task_id)}", params={"wait": wait})
        return Task.from_dict(resp)

    def cancel(self, callee: str, task_id: str) -> Task:
        """Cancel a task you opened (A2A ``CancelTask``). Idempotent."""
        return Task.from_dict(self._t.request("POST", f"/v1/agents/{seg(callee)}/tasks/{seg(task_id)}/cancel"))

    # --- The callee side: pull delivery --------------------------------------
    def next_task(self, *, wait: float = 20.0) -> Optional[Task]:
        """Claim the next task opened on THIS agent, long-polling up to
        ``wait`` seconds. None when there is none. The claim is exclusive:
        N replicas polling at once each get a different task."""
        resp = self._t.request("GET", "/v1/agents/me/tasks/next", params={"wait": wait})
        return Task.from_dict(resp) if resp else None

    def reply(self, task_id: str, reply: TaskReply) -> Task:
        """Finish, pause, or fail a task this agent claimed."""
        resp = self._t.request("POST", f"/v1/agents/me/tasks/{seg(task_id)}/result", json_body=reply.to_dict())
        return Task.from_dict(resp)

    def serve(
        self,
        handler: Callable[[Task], Any],
        *,
        wait: float = 20.0,
        max_tasks: Optional[int] = None,
        stop: Optional[Callable[[], bool]] = None,
    ) -> int:
        """Run this agent as a callee: claim tasks, hand each to ``handler``,
        post its reply. Blocks; returns how many tasks were handled.

        ``handler(task)`` returns a :class:`TaskReply` (``task.done(...)``,
        ``task.ask(...)``, ``task.fail(...)``), or a plain ``str`` / ``dict``
        / list of artifacts, which completes the task. An exception fails the
        task with its message. Inside the handler, make your governed calls
        with ``parent_call_id=task.call_id`` so they hang under the task.

        No inbound port is needed — this is a pull loop against the door.
        ``stop()`` is polled between tasks for a clean shutdown.
        """
        handled = 0
        while max_tasks is None or handled < max_tasks:
            if stop is not None and stop():
                break
            task = self.next_task(wait=wait)
            if task is None:
                continue
            try:
                out = handler(task)
                reply = _coerce_reply(task, out)
            except Exception as exc:  # noqa: BLE001 - a failing handler fails the task, not the loop
                reply = task.fail(f"{exc.__class__.__name__}: {exc}")
            self.reply(task.id, reply)
            handled += 1
        return handled


def _agent_message_body(
    message: str,
    stream: bool,
    top_k: Optional[int],
    dimensions: Optional[Mapping[str, str]],
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"message": message}
    if stream:
        body["stream"] = True
    if top_k is not None:
        body["top_k"] = top_k
    if dimensions:
        body["dimensions"] = dict(dimensions)
    return body


AudioFile = Union[bytes, bytearray, str, "os.PathLike[str]", Any]


def _read_audio(file: AudioFile) -> tuple:
    """Normalise ``file`` to ``(filename, bytes, content_type | None)``.

    Accepts raw bytes, a path, an open binary file object, or a
    ``(filename, bytes[, content_type])`` tuple. Always read into memory so
    retries can replay the body; the control plane caps audio length anyway."""
    if isinstance(file, tuple):
        name, content = file[0], file[1]
        ctype = file[2] if len(file) > 2 else None
        if hasattr(content, "read"):
            content = content.read()
        return name, bytes(content), ctype
    if isinstance(file, (bytes, bytearray)):
        return "audio", bytes(file), None
    if isinstance(file, (str, os.PathLike)):
        with open(file, "rb") as fh:
            return os.path.basename(os.fspath(file)), fh.read(), None
    if hasattr(file, "read"):
        name = os.path.basename(str(getattr(file, "name", "audio"))) or "audio"
        return name, file.read(), None
    raise TypeError("file must be bytes, a path, a binary file object or (filename, bytes) tuple")


def _transcription_request(
    file: AudioFile,
    model: str,
    language: Optional[str],
    prompt: Optional[str],
    response_format: Optional[str],
    metadata: Optional[Mapping[str, Any]],
    session: Optional[str],
) -> Dict[str, Any]:
    name, content, ctype = _read_audio(file)
    data: Dict[str, str] = {"model": model}
    if language is not None:
        data["language"] = language
    if prompt is not None:
        data["prompt"] = prompt
    if response_format is not None:
        data["response_format"] = response_format
    if metadata:
        data["metadata"] = json.dumps(dict(metadata))
    part = (name, content, ctype) if ctype else (name, content)
    return {"data": data, "files": {"file": part}, "headers": {SESSION_HEADER: session}}


def _parse_transcription(response: Any) -> Transcription:
    trace_id = response.headers.get(AUDIO_TRACE_ID_HEADER)
    if "json" in response.headers.get("content-type", ""):
        return Transcription.from_dict(response.json(), trace_id)
    return Transcription(text=response.text, trace_id=trace_id)


def _speech_request(
    model: str,
    input: str,
    voice: str,
    response_format: Optional[str],
    speed: Optional[float],
    metadata: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"model": model, "input": input, "voice": voice}
    if response_format is not None:
        body["response_format"] = response_format
    if speed is not None:
        body["speed"] = speed
    if metadata:
        body["metadata"] = dict(metadata)
    return body


def _parse_speech(response: Any) -> SpeechAudio:
    return SpeechAudio(
        audio=response.content,
        content_type=response.headers.get("content-type"),
        trace_id=response.headers.get(AUDIO_TRACE_ID_HEADER),
    )


def _register_body(**kw: Any) -> Dict[str, Any]:
    body = {k: v for k, v in kw.items() if v is not None and not isinstance(v, _Unset)}
    if kw.get("allowed_models") is None:  # explicit null = unrestricted key
        body["allowed_models"] = None
    elif isinstance(kw.get("allowed_models"), _Unset):
        body.pop("allowed_models", None)
    return body


def _omit_none(**kwargs: Any) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def _policy_body(fields: Mapping[str, Any]) -> Dict[str, Any]:
    """GET returns read-only ``updated_at``; PUT rejects unknown fields, so a
    read-modify-write of the GET result must drop it."""
    return {k: v for k, v in fields.items() if v is not None and k != "updated_at"}


def _asset_body(**kw: Any) -> Dict[str, Any]:
    return _omit_none(**kw)


class Transcriptions:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def create(
        self,
        file: AudioFile,
        model: str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        response_format: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        session: Optional[str] = None,
    ) -> Transcription:
        """Transcription: ``POST /v1/audio/transcriptions`` (multipart).

        A Governed Call measured in audio seconds. ``file`` is bytes, a path,
        an open binary file, or ``(filename, bytes[, content_type])``.
        ``session`` sets ``X-Forgebench-Session`` (the Voice Conversation
        key; ``metadata={"session": ...}`` also works and wins). Raises
        :class:`BudgetExceededError` (402) before any provider call when the
        budget is spent, and :class:`PermissionDeniedError` with
        ``code == "model_not_allowed"`` for a model outside the allowlist.
        """
        req = _transcription_request(
            file, model, language, prompt, response_format, metadata, session
        )
        resp = self._t.request_raw("POST", "/v1/audio/transcriptions", **req)
        return _parse_transcription(resp)


class Speech:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def create(
        self,
        model: str,
        input: str,
        voice: str,
        response_format: Optional[str] = None,
        speed: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        session: Optional[str] = None,
    ) -> SpeechAudio:
        """Speech Synthesis: ``POST /v1/audio/speech``.

        A Governed Call measured in characters. Returns the audio bytes with
        the trace id from ``X-Forgebench-Trace-Id``. The voice policy may
        refuse the call: :class:`ValidationError` (``disclosure_missing``) or
        :class:`PermissionDeniedError` (``voice_not_allowed``,
        ``marking_required``).
        """
        body = _speech_request(model, input, voice, response_format, speed, metadata)
        resp = self._t.request_raw(
            "POST", "/v1/audio/speech", json_body=body, headers={SESSION_HEADER: session}
        )
        return _parse_speech(resp)


class Audio:
    def __init__(self, transport: SyncTransport) -> None:
        self.transcriptions = Transcriptions(transport)
        self.speech = Speech(transport)


class VoicePolicy:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def get(self) -> Dict[str, Any]:
        """The workspace voice policy (defaults when none is saved)."""
        return self._t.request("GET", "/v1/voice/policy")

    def update(self, **fields: Any) -> Dict[str, Any]:
        """Replace the voice policy (admin). Fields: ``disclosure_mode``
        (``log`` | ``enforce`` | ``inject``), ``disclosure_phrases``,
        ``inject_text``, ``default_language``, ``marking_required``,
        ``marking_min_level``, ``transcript_guardrails``,
        ``voice_assets_required_for``, ``voice_trace_content`` (default off:
        when on, gateway transcripts/TTS text AND customer telemetry content
        are recorded). Omitted fields reset to defaults. ``update(**get())``
        works: the read-only ``updated_at`` is dropped."""
        return self._t.request("PUT", "/v1/voice/policy", json_body=_policy_body(fields))


class VoiceAssets:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def list(self) -> List[Dict[str, Any]]:
        data = self._t.request("GET", "/v1/voice/assets")
        return data if isinstance(data, list) else (data or {}).get("data", [])

    def create(
        self,
        *,
        provider: str,
        provider_voice_id: str,
        display_name: str,
        kind: str,
        consent_record_id: Optional[str] = None,
        licensed_uses: Optional[List[str]] = None,
        allowed_agent_ids: Optional[List[str]] = None,
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a Voice Asset (admin). ``kind`` is ``stock``, ``brand`` or
        ``cloned``; a cloned Voice Asset needs ``consent_record_id``. A
        duplicate ``(provider, provider_voice_id)`` raises
        :class:`ConflictError`."""
        body = _asset_body(
            provider=provider,
            provider_voice_id=provider_voice_id,
            display_name=display_name,
            kind=kind,
            consent_record_id=consent_record_id,
            licensed_uses=licensed_uses,
            allowed_agent_ids=allowed_agent_ids,
            expires_at=expires_at,
        )
        return self._t.request("POST", "/v1/voice/assets", json_body=body)

    def update(self, asset_id: str, **fields: Any) -> Dict[str, Any]:
        """Patch ``display_name``, ``consent_record_id``, ``licensed_uses``,
        ``allowed_agent_ids`` or ``expires_at``. Only the fields you pass are
        sent; pass ``None`` explicitly to clear one (e.g. ``expires_at=None``)."""
        return self._t.request(
            "PATCH", f"/v1/voice/assets/{seg(asset_id)}", json_body=fields
        )

    def revoke(self, asset_id: str) -> Dict[str, Any]:
        return self._t.request("POST", f"/v1/voice/assets/{seg(asset_id)}/revoke")


class VoiceConsents:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def list(self) -> List[Dict[str, Any]]:
        data = self._t.request("GET", "/v1/voice/consents")
        return data if isinstance(data, list) else (data or {}).get("data", [])

    def create(
        self,
        *,
        subject_name: str,
        purpose: str,
        scope: str,
        subject_contact: Optional[str] = None,
        evidence_uri: Optional[str] = None,
        granted_at: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a Consent Record (admin). ``purpose`` is ``voice_clone`` or
        ``recording``; ``granted_at`` defaults to now on the server."""
        body = _omit_none(
            subject_name=subject_name,
            purpose=purpose,
            scope=scope,
            subject_contact=subject_contact,
            evidence_uri=evidence_uri,
            granted_at=granted_at,
            expires_at=expires_at,
        )
        return self._t.request("POST", "/v1/voice/consents", json_body=body)

    def revoke(self, consent_id: str) -> Dict[str, Any]:
        return self._t.request("POST", f"/v1/voice/consents/{seg(consent_id)}/revoke")


class Voice:
    """Voice governance admin: policy, Voice Assets, Consent Records."""

    def __init__(self, transport: SyncTransport) -> None:
        self.policy = VoicePolicy(transport)
        self.assets = VoiceAssets(transport)
        self.consents = VoiceConsents(transport)


def _coerce_reply(task: Task, out: Any) -> TaskReply:
    if isinstance(out, TaskReply):
        return out
    if out is None:
        return task.done(text="")
    if isinstance(out, str):
        return task.done(text=out)
    if isinstance(out, dict):
        return task.done(data=out)
    if isinstance(out, list):
        return task.done(artifacts=out)
    return task.done(text=str(out))


class Runs:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def create(
        self,
        *,
        agent_id: Optional[str] = None,
        model: str = "mock-gpt",
        input: Optional[Dict[str, Any]] = None,
    ) -> Run:
        body: Dict[str, Any] = {"model": model, "input": input or {}}
        if agent_id is not None:
            body["agent_id"] = agent_id
        data = self._t.request("POST", "/v1/runs", json_body=body)
        return Run.from_dict(data)

    def get(self, run_id: str) -> Run:
        data = self._t.request("GET", f"/v1/runs/{seg(run_id)}")
        return Run.from_dict(data)

    def wait(
        self,
        run_id: str,
        *,
        timeout: Optional[float] = None,
        poll_interval: float = 0.5,
    ) -> Run:
        """Poll until terminal; timeout stops only waiting, and defaults to no deadline."""
        return _wait_run(self._t, run_id, timeout=timeout, poll_interval=poll_interval)


def _wait_run(
    transport: SyncTransport,
    run_id: str,
    *,
    timeout: Optional[float],
    poll_interval: float,
) -> Run:
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        data = transport.request("GET", f"/v1/runs/{seg(run_id)}")
        run = Run.from_dict(data)
        if run.is_terminal:
            return run
        if deadline is not None and time.monotonic() >= deadline:
            from ._exceptions import ForgebenchError

            raise ForgebenchError(
                f"Timed out waiting {timeout}s for run {run_id} (last status: {run.status}). "
                f"Polling stopped; the run was not cancelled. Retrieve or wait for {run_id} again."
            )
        time.sleep(poll_interval if deadline is None else min(poll_interval, max(0, deadline - time.monotonic())))


# Account-level helpers exposed on the client for convenience / scripting.
class Account:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def whoami(self) -> WhoAmI:
        data = self._t.request("GET", "/v1/auth/whoami")
        return WhoAmI.from_dict(data)

    def metering_summary(self) -> MeteringSummary:
        data = self._t.request("GET", "/v1/metering/summary")
        return MeteringSummary.from_dict(data)


class AgentTools:
    """The agent-facing MCP surface (``/v1/agent-tools*``). Requires an
    AGENT credential — the API key this client was constructed with must
    have been issued to a registered agent (``api_keys.agent_id`` set on the
    control plane); a human/developer key has no allowlist and 403s here.
    """

    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def list(self) -> List[ToolBinding]:
        """Every tool the calling agent may currently call, resolved from its
        allowlist server-side — build your tool list from this rather than
        hardcoding it, so a central revoke actually reaches this agent."""
        data = self._t.request("GET", "/v1/agent-tools")
        items = (data or {}).get("tools", [])
        return [ToolBinding.from_dict(t) for t in items]

    def openai_schema(
        self,
        *,
        parameters: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """The agent's CURRENT allowlist as an OpenAI ``tools`` array, ready to
        pass as ``extra_body={"tools": ...}`` on ``chat.completions.create``.

        Built from :meth:`list` on every call, so a binding revoked centrally
        drops out of the next turn's tool list — nothing is cached. The
        control plane records only a tool's name and description; its input
        schema lives with the agent's own MCP client, so pass ``parameters``
        (``{tool_name: json_schema}``) for the tools you want the model to
        see typed arguments for. Unlisted tools get a permissive object
        schema, which is what an MCP server with no declared schema means.
        """
        params = dict(parameters or {})
        return [
            {
                "type": "function",
                "function": {
                    "name": b.tool,
                    "description": b.description or "",
                    "parameters": dict(
                        params.get(b.tool)
                        or {"type": "object", "properties": {}, "additionalProperties": True}
                    ),
                },
            }
            for b in self.list()
        ]

    def report(
        self,
        *,
        call_id: str,
        tool_name: str,
        result: Any = None,
        error: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> ToolOutcomeReceipt:
        """Record what a tool call the gate allowed actually returned.

        The control plane recorded the DECISION when it relayed the model's
        ``tool_calls``; your MCP client did the call. This completes that
        same ledger row with the outcome, so "what did this agent get back"
        has one answer next to "was it allowed to ask". ``call_id`` is the
        chat response that carried the tool_call. Client-asserted, and stored
        as such — it never changes the decision.
        """
        data = self._t.request(
            "POST",
            "/v1/agent-tools/report",
            json_body={
                "call_id": call_id,
                "tool_name": tool_name,
                "result": result,
                "error": error,
                "latency_ms": latency_ms,
            },
        )
        return ToolOutcomeReceipt.from_dict(data or {})

    def dispatch(
        self,
        tool_calls: Optional[List[Any]],
        execute: Callable[[str, Dict[str, Any]], Any],
        *,
        call_id: str,
    ) -> List[Dict[str, Any]]:
        """Run every tool_call in a governed response and report each outcome.

        ``execute(name, arguments)`` is YOUR tool runner — typically a thin
        wrapper over your MCP client. For each call this executes it, reports
        the result or the exception to the control plane against ``call_id``
        (the response the tool_calls came from), and returns the
        ``role: "tool"`` messages to append before the next model turn. An
        exception from ``execute`` is reported as the tool's error and
        surfaced to the model as ``{"error": ...}`` rather than aborting the
        loop — the model gets to decide what to do about a failed tool. Each
        outcome is reported exactly once; a failed ``report`` is logged and
        never changes the returned messages.
        """
        messages: List[Dict[str, Any]] = []
        for tc in tool_calls or []:
            tc_id, name, arguments = _parse_tool_call(tc)
            started = time.monotonic()
            error: Optional[str] = None
            try:
                result = execute(name, arguments)
                content: Any = result
            except Exception as exc:  # noqa: BLE001 - the model decides what a failed tool means
                result, error = None, str(exc)[:4000]
                content = {"error": str(exc)}
            latency = int((time.monotonic() - started) * 1000)
            try:
                self.report(call_id=call_id, tool_name=name, result=result, error=error, latency_ms=latency)
            except Exception as exc:  # noqa: BLE001 - reporting is bookkeeping, never the tool's outcome
                logger.warning("forgebench: report for tool %s failed: %s", name, exc)
            messages.append(_tool_message(tc_id, name, content))
        return messages


def _parse_tool_call(tc: Any) -> tuple[Optional[str], str, Dict[str, Any]]:
    """(id, name, arguments) from an OpenAI-shaped tool_call — a dict from the
    wire, or an object with ``.id`` / ``.function.name`` / ``.function.arguments``.
    ``arguments`` is a JSON string on the wire; a dict is accepted as-is and an
    unparseable string becomes ``{"_raw": ...}`` so the tool still sees it."""
    if isinstance(tc, dict):
        fn = tc.get("function") or {}
        tc_id, name, raw_args = tc.get("id"), fn.get("name"), fn.get("arguments")
    else:
        fn = getattr(tc, "function", None)
        tc_id = getattr(tc, "id", None)
        name = getattr(fn, "name", None)
        raw_args = getattr(fn, "arguments", None)
    if not name:
        raise ValueError("tool_call has no function.name")
    if isinstance(raw_args, dict):
        arguments: Dict[str, Any] = raw_args
    elif raw_args in (None, ""):
        arguments = {}
    else:
        try:
            parsed = json.loads(raw_args)
            arguments = parsed if isinstance(parsed, dict) else {"_raw": parsed}
        except (TypeError, ValueError):
            arguments = {"_raw": raw_args}
    return tc_id, str(name), arguments


def _tool_message(tc_id: Optional[str], name: str, content: Any) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "role": "tool",
        "name": name,
        "content": content if isinstance(content, str) else json.dumps(content, default=str),
    }
    if tc_id:
        msg["tool_call_id"] = tc_id
    return msg


# --- Workflows -------------------------------------------------------------------------------
def _workflow_body(**kw: Any) -> Dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}


def _step_request(
    keys: Mapping[str, str],
    step: str,
    parent: Optional[str],
    *,
    messages: List[MessageLike],
    model: str,
    stream: bool,
    temperature: Optional[float],
    max_tokens: Optional[int],
    trace_id: Optional[str],
    extra_body: Optional[Mapping[str, Any]],
    metadata: Optional[Mapping[str, Any]],
    session_id: Optional[str],
    user: Optional[str],
    tags: Optional[List[str]],
    dimensions: Optional[Mapping[str, str]],
) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
    """The chat payload and headers for one step's call: THAT step's key as the
    bearer token (per-request headers win over the client's own key) and the
    previous call's ``call_id`` as ``X-Parent-Call`` (none for the first call)."""
    payload = _chat_payload(
        model=model, messages=messages, stream=stream, temperature=temperature, max_tokens=max_tokens,
        trace_id=trace_id, extra=extra_body, metadata=metadata, session_id=session_id, user=user,
        tags=tags, dimensions=dimensions,
    )
    return payload, {"Authorization": f"Bearer {keys[step]}", PARENT_CALL_HEADER: parent}


def _check_keys(slug: str, keys: Mapping[str, str]) -> Dict[str, str]:
    bad = [name for name, key in keys.items() if not isinstance(key, str) or not key]
    if bad:
        raise ValueError(f"workflow {slug!r}: no api key for step(s) {', '.join(bad)}")
    return dict(keys)


class Workflows:
    """``/v1/workflows``: a named group of agents (steps) registered and observed
    together. Methods return the API's JSON. Registering mints one agent and key
    per step; the raw keys are in the response ONCE (``credentials[].credential.api_key``)."""

    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

    def list(self) -> List[Dict[str, Any]]:
        return self._t.request("GET", "/v1/workflows")

    def get(self, slug: str, *, days: Optional[int] = None) -> Dict[str, Any]:
        """Steps, declared and observed edges. ``days`` (1-365) is the window observed traffic is read over."""
        return self._t.request("GET", f"/v1/workflows/{seg(slug)}", params=_omit_none(days=days))

    def checks(self, slug: str) -> Dict[str, Any]:
        """Is it wired, threaded (X-Parent-Call) and seeing traffic? Each non-passing check has a ``fix``."""
        return self._t.request("GET", f"/v1/workflows/{seg(slug)}/checks")

    def register(
        self,
        *,
        name: str,
        owner_identity_id: str,
        steps: List[Mapping[str, Any]],
        edges: Optional[List[Mapping[str, str]]] = None,
        slug: Optional[str] = None,
        description: Optional[str] = None,
        issue_credential: bool = True,
    ) -> Dict[str, Any]:
        """Register a workflow atomically: every step's agent and key, the steps and the edges, or nothing.

        ``steps``: ``[{"name": "grading", "model": "...", "allowed_models": [...], "monthly_budget_usd": 5}]``.
        ``edges``: ``[{"from": "grading", "to": "retry", "kind": "sequence" | "task"}]`` (step names).
        ``issue_credential=False`` registers every agent SEALED (no keys in the response).
        """
        body = _workflow_body(
            name=name, slug=slug, description=description, owner_identity_id=owner_identity_id,
            steps=[dict(s) for s in steps], edges=[dict(e) for e in edges or []],
        )
        if not issue_credential:
            body["issue_credential"] = False
        return self._t.request("POST", "/v1/workflows", json_body=body)

    def suggestions(self) -> List[Dict[str, Any]]:
        """Suggested workflows: groups of existing agents that belong together (from scanned code and observed traffic)."""
        return self._t.request("GET", "/v1/workflows/suggestions")

    def accept_suggestion(
        self,
        fingerprint: str,
        *,
        name: Optional[str] = None,
        slug: Optional[str] = None,
        description: Optional[str] = None,
        owner_identity_id: Optional[str] = None,
        edges: Optional[List[Mapping[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Create a workflow from a suggestion: its existing agents become steps (no agent or key is created).
        ``owner_identity_id`` defaults to the caller; naming another member is admin-only.
        ``edges`` replaces the suggested ones: ``[{"from_agent_id": ..., "to_agent_id": ..., "kind": "sequence" | "task"}]``."""
        body = _workflow_body(
            name=name, slug=slug, description=description, owner_identity_id=owner_identity_id,
            edges=[dict(e) for e in edges] if edges is not None else None,
        )
        return self._t.request("POST", f"/v1/workflows/suggestions/{seg(fingerprint)}/accept", json_body=body)

    def dismiss_suggestion(self, fingerprint: str) -> Dict[str, Any]:
        """Hide a suggestion; it stays hidden. Returns ``{"fingerprint": ..., "status": "dismissed"}``."""
        return self._t.request("POST", f"/v1/workflows/suggestions/{seg(fingerprint)}/dismiss")

    def update(self, slug: str, *, name: Optional[str] = None, description: Optional[str] = None) -> Dict[str, Any]:
        return self._t.request(
            "PATCH", f"/v1/workflows/{seg(slug)}", json_body=_workflow_body(name=name, description=description)
        )

    def archive(self, slug: str) -> Dict[str, Any]:
        """Archive it and release its steps. Agents and keys are untouched; returns ``{"binding_retained": bool}``."""
        return self._t.request("DELETE", f"/v1/workflows/{seg(slug)}")

    def add_step(
        self,
        slug: str,
        *,
        name: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        description: Optional[str] = None,
        allowed_models: Optional[List[str]] = None,
        monthly_budget_usd: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add a step: a new agent (``name`` + ``model``) or an existing one (``agent_id``)."""
        body = _workflow_body(
            name=name, model=model, agent_id=agent_id, description=description,
            allowed_models=allowed_models, monthly_budget_usd=monthly_budget_usd,
        )
        return self._t.request("POST", f"/v1/workflows/{seg(slug)}/steps", json_body=body)

    def remove_step(self, slug: str, step: str) -> Dict[str, Any]:
        """Detach a step (its edges go with it). The agent is untouched; returns ``{"binding_retained": bool}``."""
        return self._t.request("DELETE", f"/v1/workflows/{seg(slug)}/steps/{seg(step)}")

    def add_edge(self, slug: str, *, from_step: str, to_step: str, kind: Literal["sequence", "task"]) -> Dict[str, Any]:
        return self._t.request(
            "POST", f"/v1/workflows/{seg(slug)}/edges", json_body={"from": from_step, "to": to_step, "kind": kind}
        )

    def remove_edge(self, slug: str, edge_id: str, *, revoke_binding: bool = False) -> Dict[str, Any]:
        """Remove a declared edge. A task edge's agent binding is kept unless ``revoke_binding=True``."""
        return self._t.request(
            "DELETE", f"/v1/workflows/{seg(slug)}/edges/{seg(edge_id)}",
            params={"revoke_binding": "true"} if revoke_binding else None,
        )


class WorkflowStep:
    """One step of a :class:`WorkflowRun`: chat with THIS step's key; the
    run supplies ``parent_call_id``."""

    def __init__(self, run: "WorkflowRun", name: str) -> None:
        self._run = run
        self.name = name

    def chat(
        self,
        *,
        messages: List[MessageLike],
        model: str = "mock-gpt",
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        trace_id: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        session_id: Optional[str] = None,
        user: Optional[str] = None,
        tags: Optional[List[str]] = None,
        dimensions: Optional[Mapping[str, str]] = None,
    ) -> Union[ChatCompletion, ChatStream]:
        """Like ``client.chat.completions.create`` with this step's key. The call is
        recorded as the child of the run's previous call (the first call is a root).
        A streamed call becomes the next call's parent once its ``call_id`` is known,
        so read the stream to the end before the next step."""
        run = self._run
        payload, headers = _step_request(
            run._keys, self.name, run.last_call_id, messages=messages, model=model, stream=stream,
            temperature=temperature, max_tokens=max_tokens, trace_id=trace_id, extra_body=extra_body,
            metadata=metadata, session_id=session_id, user=user, tags=tags, dimensions=dimensions,
        )
        if stream:
            out: Union[ChatCompletion, ChatStream] = ChatStream(run._t, payload, headers)
        else:
            out = ChatCompletion.from_dict(
                run._t.request("POST", "/v1/chat/completions", json_body=payload, headers=headers)
            )
        run._record(out)
        return out


class WorkflowRun(AbstractContextManager):
    """One execution of a workflow: ``with wf.run() as run: run.step("grading").chat(...)``.
    Every call names the previous call of the run as its parent, so the ledger
    holds the run as a chain. ``root_call_id`` is the first call's id."""

    def __init__(self, transport: SyncTransport, slug: str, keys: Mapping[str, str]) -> None:
        self._t = transport
        self.slug = slug
        self._keys = keys
        self._calls: List[Any] = []

    def step(self, name: str) -> WorkflowStep:
        if name not in self._keys:
            raise ValueError(f"workflow {self.slug!r}: no api key for step {name!r} (keys: {', '.join(self._keys)})")
        return WorkflowStep(self, name)

    def _record(self, call: Any) -> None:
        self._calls.append(call)

    @property
    def call_ids(self) -> List[str]:
        """The ``call_id`` of every call so far, in order (a stream's, once known)."""
        return [c.call_id for c in self._calls if c.call_id]

    @property
    def last_call_id(self) -> Optional[str]:
        """The newest call's id that is known: a call without one (an old server, a
        stream not read yet) does not break the chain."""
        return next((c.call_id for c in reversed(self._calls) if c.call_id), None)

    @property
    def root_call_id(self) -> Optional[str]:
        return self._calls[0].call_id if self._calls else None

    def __exit__(self, *exc: Any) -> None:
        return None


class Workflow:
    """``client.workflow("question-paper", keys={"grading": "sk_..."})``: a registered
    workflow with one key per step (from ``workflows.register`` or the console)."""

    def __init__(self, transport: SyncTransport, slug: str, keys: Mapping[str, str]) -> None:
        self._t = transport
        self.slug = slug
        self._keys = _check_keys(slug, keys)

    @property
    def steps(self) -> List[str]:
        return list(self._keys)

    def run(self) -> WorkflowRun:
        return WorkflowRun(self._t, self.slug, self._keys)
