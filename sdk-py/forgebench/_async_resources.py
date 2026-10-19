"""Asynchronous mirror of :mod:`forgebench._resources` (chat, agents, runs).

Same endpoint mapping and semantics as the sync resources; methods are
``async`` and streaming yields via ``async for``.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Literal, overload, Any, Awaitable, Callable, AsyncIterator, Dict, List, Mapping, Optional, Union

from ._resources import (
    _UNSET,
    AudioFile,
    MessageLike,
    _agent_message_body,
    _omit_none,
    _policy_body,
    _parse_speech,
    _parse_transcription,
    _speech_request,
    _transcription_request,
    _chat_payload,
    _coerce_reply,
    _check_keys,
    _in_current_trace,
    _parse_tool_call,
    _register_body,
    _step_request,
    _workflow_body,
    _StreamIds,
    _tool_message,
    logger,
)
from ._trace import PARENT_CALL_HEADER, PARENT_OBSERVATION_HEADER, SESSION_HEADER
from ._transport import AsyncTransport, seg
from .tracing._tracer import Tracer
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


class AsyncChatStream(_StreamIds):
    """Async iterator of :class:`ChatCompletionChunk`; ``trace_id`` / ``call_id``
    as on :class:`forgebench.ChatStream`."""

    def __init__(self, transport: AsyncTransport, payload: Dict[str, Any], headers: Mapping[str, Any]) -> None:
        self._events = transport.stream_sse(
            "POST", "/v1/chat/completions", json_body=payload, headers=headers,
            on_response=self._on_response,
        )

    def __aiter__(self) -> "AsyncChatStream":
        return self

    async def __anext__(self) -> ChatCompletionChunk:
        while True:
            event = await self._events.__anext__()
            if not self._take_meta(event):
                return ChatCompletionChunk.from_dict(event)

    async def aclose(self) -> None:
        await self._events.aclose()


class AsyncCompletions:
    def __init__(self, transport: AsyncTransport, tracer: Optional[Tracer] = None) -> None:
        self._t = transport
        self._tracer = tracer

    async def create(
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
    ) -> Union[ChatCompletion, AsyncChatStream]:
        """See :meth:`forgebench._resources.Completions.create` — identical
        semantics; ``stream=True`` returns an :class:`AsyncChatStream`."""
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
            return AsyncChatStream(self._t, payload, headers)
        data = await self._t.request(
            "POST", "/v1/chat/completions", json_body=payload, headers=headers
        )
        return ChatCompletion.from_dict(data)


class AsyncChat:
    def __init__(self, transport: AsyncTransport, tracer: Optional[Tracer] = None) -> None:
        self.completions = AsyncCompletions(transport, tracer)


class AsyncAgents:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def list(self) -> List[Agent]:
        data = await self._t.request("GET", "/v1/agents")
        items = data if isinstance(data, list) else (data or {}).get("data", [])
        return [Agent.from_dict(a) for a in items]

    async def create(
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
        """See :meth:`forgebench._resources.Agents.create` — ``owner_identity_id``
        is required (F1); the control plane rejects an agent with no named
        owner."""
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
        data = await self._t.request("POST", "/v1/agents", json_body=body)
        return Agent.from_dict(data)

    async def register(
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
        """See :meth:`forgebench._resources.Agents.register`."""
        return await self._t.request("POST", "/v1/agents/register", json_body=_register_body(
            name=name, owner_identity_id=owner_identity_id, model=model, description=description,
            tags=tags, system_prompt=system_prompt, config=config, monthly_budget_usd=monthly_budget_usd,
            rpm_limit=rpm_limit, daily_limit_usd=daily_limit_usd, allowed_models=allowed_models,
            issue_credential=issue_credential,
        ))

    @overload
    async def run(self, agent_id: str, *, message: str, stream: Literal[True],
        input: Optional[Dict[str, Any]] = ...,
        model: Optional[str] = ...,
        wait: bool = ...,
        timeout: Optional[float] = ...,
        poll_interval: float = ...,
        top_k: Optional[int] = ...,
        dimensions: Optional[Mapping[str, str]] = ...,
        session: Optional[str] = ...,
    ) -> AsyncIterator[Dict[str, Any]]: ...

    @overload
    async def run(self, agent_id: str, *, message: str, stream: Literal[False] = ...,
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
    async def run(self, agent_id: str, *, message: None = ..., stream: bool = ...,
        input: Optional[Dict[str, Any]] = ...,
        model: Optional[str] = ...,
        wait: bool = ...,
        timeout: Optional[float] = ...,
        poll_interval: float = ...,
        top_k: Optional[int] = ...,
        dimensions: Optional[Mapping[str, str]] = ...,
        session: Optional[str] = ...,
    ) -> Run: ...

    async def run(
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
    ) -> Union[Run, Dict[str, Any], AsyncIterator[Dict[str, Any]]]:
        """See :meth:`forgebench._resources.Agents.run`. With ``message`` and
        ``stream=True``, ``await`` the call and iterate the result with
        ``async for`` (mirrors ``chat.completions.create(stream=True)``)."""
        if message is not None:
            body = _agent_message_body(message, stream, top_k, dimensions)
            path = f"/v1/agents/{seg(agent_id)}/run"
            headers = {SESSION_HEADER: session}
            if stream:
                return self._t.stream_sse(
                    "POST", path, json_body=body, headers=headers, require_terminal=True
                )
            return await self._t.request("POST", path, json_body=body, headers=headers)
        body = {"input": input or {}}
        if agent_id is not None:
            body["agent_id"] = agent_id
        if model is not None:
            body["model"] = model
        data = await self._t.request("POST", "/v1/runs", json_body=body)
        run = Run.from_dict(data)
        if wait and not run.is_terminal:
            return await _await_run(self._t, run.id, timeout=timeout, poll_interval=poll_interval)
        return run

    # --- Agent -> agent through the door (A2A tasks) ---------------------------
    async def call(
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
        """See :meth:`forgebench._resources.Agents.call`."""
        body: Dict[str, Any] = {
            "message": {"role": "user", "parts": message_parts(text=text, data=data, parts=parts)},
        }
        if context_id:
            body["context_id"] = context_id
        resp = await self._t.request(
            "POST",
            f"/v1/agents/{seg(callee)}/tasks",
            json_body=body,
            params={"wait": wait},
            headers={PARENT_CALL_HEADER: parent_call_id},
        )
        return Task.from_dict(resp)

    async def task(self, callee: str, task_id: str, *, wait: float = 0.0) -> Task:
        resp = await self._t.request("GET", f"/v1/agents/{seg(callee)}/tasks/{seg(task_id)}", params={"wait": wait})
        return Task.from_dict(resp)

    async def cancel(self, callee: str, task_id: str) -> Task:
        return Task.from_dict(await self._t.request("POST", f"/v1/agents/{seg(callee)}/tasks/{seg(task_id)}/cancel"))

    async def next_task(self, *, wait: float = 20.0) -> Optional[Task]:
        resp = await self._t.request("GET", "/v1/agents/me/tasks/next", params={"wait": wait})
        return Task.from_dict(resp) if resp else None

    async def reply(self, task_id: str, reply: TaskReply) -> Task:
        resp = await self._t.request(
            "POST", f"/v1/agents/me/tasks/{seg(task_id)}/result", json_body=reply.to_dict()
        )
        return Task.from_dict(resp)

    async def serve(
        self,
        handler: Callable[[Task], Union[Any, Awaitable[Any]]],
        *,
        wait: float = 20.0,
        max_tasks: Optional[int] = None,
        stop: Optional[Callable[[], bool]] = None,
    ) -> int:
        """See :meth:`forgebench._resources.Agents.serve`. ``handler`` may be
        sync or async."""
        handled = 0
        while max_tasks is None or handled < max_tasks:
            if stop is not None and stop():
                break
            task = await self.next_task(wait=wait)
            if task is None:
                continue
            try:
                out = handler(task)
                if inspect.isawaitable(out):
                    out = await out
                reply = _coerce_reply(task, out)
            except Exception as exc:  # noqa: BLE001 - a failing handler fails the task, not the loop
                reply = task.fail(f"{exc.__class__.__name__}: {exc}")
            await self.reply(task.id, reply)
            handled += 1
        return handled


class AsyncRuns:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def create(
        self,
        *,
        agent_id: Optional[str] = None,
        model: str = "mock-gpt",
        input: Optional[Dict[str, Any]] = None,
    ) -> Run:
        body: Dict[str, Any] = {"model": model, "input": input or {}}
        if agent_id is not None:
            body["agent_id"] = agent_id
        data = await self._t.request("POST", "/v1/runs", json_body=body)
        return Run.from_dict(data)

    async def get(self, run_id: str) -> Run:
        data = await self._t.request("GET", f"/v1/runs/{seg(run_id)}")
        return Run.from_dict(data)

    async def wait(
        self,
        run_id: str,
        *,
        timeout: Optional[float] = None,
        poll_interval: float = 0.5,
    ) -> Run:
        """Poll until terminal; timeout stops only waiting, and defaults to no deadline."""
        return await _await_run(self._t, run_id, timeout=timeout, poll_interval=poll_interval)


async def _await_run(
    transport: AsyncTransport,
    run_id: str,
    *,
    timeout: Optional[float],
    poll_interval: float,
) -> Run:
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        data = await transport.request("GET", f"/v1/runs/{seg(run_id)}")
        run = Run.from_dict(data)
        if run.is_terminal:
            return run
        if deadline is not None and time.monotonic() >= deadline:
            from ._exceptions import ForgebenchError

            raise ForgebenchError(
                f"Timed out waiting {timeout}s for run {run_id} (last status: {run.status}). "
                f"Polling stopped; the run was not cancelled. Retrieve or wait for {run_id} again."
            )
        await asyncio.sleep(poll_interval if deadline is None else min(poll_interval, max(0, deadline - time.monotonic())))


class AsyncAccount:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def whoami(self) -> WhoAmI:
        data = await self._t.request("GET", "/v1/auth/whoami")
        return WhoAmI.from_dict(data)

    async def metering_summary(self) -> MeteringSummary:
        data = await self._t.request("GET", "/v1/metering/summary")
        return MeteringSummary.from_dict(data)


class AsyncAgentTools:
    """Async mirror of :class:`forgebench._resources.AgentTools` — identical
    semantics, including the ``trace_id``/``run_id`` correlation contract."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def list(self) -> List[ToolBinding]:
        data = await self._t.request("GET", "/v1/agent-tools")
        items = (data or {}).get("tools", [])
        return [ToolBinding.from_dict(t) for t in items]

    async def openai_schema(
        self,
        *,
        parameters: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """See :meth:`forgebench._resources.AgentTools.openai_schema`."""
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
            for b in await self.list()
        ]

    async def report(
        self,
        *,
        call_id: str,
        tool_name: str,
        result: Any = None,
        error: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> ToolOutcomeReceipt:
        """See :meth:`forgebench._resources.AgentTools.report`."""
        data = await self._t.request(
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

    async def dispatch(
        self,
        tool_calls: Optional[List[Any]],
        execute: Callable[[str, Dict[str, Any]], Union[Any, Awaitable[Any]]],
        *,
        call_id: str,
    ) -> List[Dict[str, Any]]:
        """See :meth:`forgebench._resources.AgentTools.dispatch`. ``execute`` may
        be sync or async; an awaitable result is awaited."""
        messages: List[Dict[str, Any]] = []
        for tc in tool_calls or []:
            tc_id, name, arguments = _parse_tool_call(tc)
            started = time.monotonic()
            error: Optional[str] = None
            try:
                result = execute(name, arguments)
                if inspect.isawaitable(result):
                    result = await result
                content: Any = result
            except Exception as exc:  # noqa: BLE001 - the model decides what a failed tool means
                result, error = None, str(exc)[:4000]
                content = {"error": str(exc)}
            latency = int((time.monotonic() - started) * 1000)
            try:
                await self.report(call_id=call_id, tool_name=name, result=result, error=error, latency_ms=latency)
            except Exception as exc:  # noqa: BLE001 - reporting is bookkeeping, never the tool's outcome
                logger.warning("forgebench: report for tool %s failed: %s", name, exc)
            messages.append(_tool_message(tc_id, name, content))
        return messages


class AsyncTranscriptions:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def create(
        self,
        file: AudioFile,
        model: str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        response_format: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        session: Optional[str] = None,
    ) -> Transcription:
        """See :meth:`forgebench._resources.Transcriptions.create`."""
        req = _transcription_request(
            file, model, language, prompt, response_format, metadata, session
        )
        resp = await self._t.request_raw("POST", "/v1/audio/transcriptions", **req)
        return _parse_transcription(resp)


class AsyncSpeech:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def create(
        self,
        model: str,
        input: str,
        voice: str,
        response_format: Optional[str] = None,
        speed: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        session: Optional[str] = None,
    ) -> SpeechAudio:
        """See :meth:`forgebench._resources.Speech.create`."""
        body = _speech_request(model, input, voice, response_format, speed, metadata)
        resp = await self._t.request_raw(
            "POST", "/v1/audio/speech", json_body=body, headers={SESSION_HEADER: session}
        )
        return _parse_speech(resp)


class AsyncAudio:
    def __init__(self, transport: AsyncTransport) -> None:
        self.transcriptions = AsyncTranscriptions(transport)
        self.speech = AsyncSpeech(transport)


class AsyncVoicePolicy:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def get(self) -> Dict[str, Any]:
        return await self._t.request("GET", "/v1/voice/policy")

    async def update(self, **fields: Any) -> Dict[str, Any]:
        return await self._t.request("PUT", "/v1/voice/policy", json_body=_policy_body(fields))


class AsyncVoiceAssets:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def list(self) -> List[Dict[str, Any]]:
        data = await self._t.request("GET", "/v1/voice/assets")
        return data if isinstance(data, list) else (data or {}).get("data", [])

    async def create(
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
        body = _omit_none(
            provider=provider,
            provider_voice_id=provider_voice_id,
            display_name=display_name,
            kind=kind,
            consent_record_id=consent_record_id,
            licensed_uses=licensed_uses,
            allowed_agent_ids=allowed_agent_ids,
            expires_at=expires_at,
        )
        return await self._t.request("POST", "/v1/voice/assets", json_body=body)

    async def update(self, asset_id: str, **fields: Any) -> Dict[str, Any]:
        return await self._t.request(
            "PATCH", f"/v1/voice/assets/{seg(asset_id)}", json_body=fields
        )

    async def revoke(self, asset_id: str) -> Dict[str, Any]:
        return await self._t.request("POST", f"/v1/voice/assets/{seg(asset_id)}/revoke")


class AsyncVoiceConsents:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def list(self) -> List[Dict[str, Any]]:
        data = await self._t.request("GET", "/v1/voice/consents")
        return data if isinstance(data, list) else (data or {}).get("data", [])

    async def create(
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
        body = _omit_none(
            subject_name=subject_name,
            purpose=purpose,
            scope=scope,
            subject_contact=subject_contact,
            evidence_uri=evidence_uri,
            granted_at=granted_at,
            expires_at=expires_at,
        )
        return await self._t.request("POST", "/v1/voice/consents", json_body=body)

    async def revoke(self, consent_id: str) -> Dict[str, Any]:
        return await self._t.request("POST", f"/v1/voice/consents/{seg(consent_id)}/revoke")


class AsyncVoice:
    def __init__(self, transport: AsyncTransport) -> None:
        self.policy = AsyncVoicePolicy(transport)
        self.assets = AsyncVoiceAssets(transport)
        self.consents = AsyncVoiceConsents(transport)


# --- Workflows -------------------------------------------------------------------------------
class AsyncWorkflows:
    """Async mirror of :class:`forgebench._resources.Workflows`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

    async def list(self) -> List[Dict[str, Any]]:
        return await self._t.request("GET", "/v1/workflows")

    async def get(self, slug: str, *, days: Optional[int] = None) -> Dict[str, Any]:
        return await self._t.request("GET", f"/v1/workflows/{seg(slug)}", params=_omit_none(days=days))

    async def checks(self, slug: str) -> Dict[str, Any]:
        return await self._t.request("GET", f"/v1/workflows/{seg(slug)}/checks")

    async def register(
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
        body = _workflow_body(
            name=name, slug=slug, description=description, owner_identity_id=owner_identity_id,
            steps=[dict(s) for s in steps], edges=[dict(e) for e in edges or []],
        )
        if not issue_credential:
            body["issue_credential"] = False
        return await self._t.request("POST", "/v1/workflows", json_body=body)

    async def update(self, slug: str, *, name: Optional[str] = None, description: Optional[str] = None) -> Dict[str, Any]:
        return await self._t.request(
            "PATCH", f"/v1/workflows/{seg(slug)}", json_body=_workflow_body(name=name, description=description)
        )

    async def archive(self, slug: str) -> Dict[str, Any]:
        return await self._t.request("DELETE", f"/v1/workflows/{seg(slug)}")

    async def add_step(
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
        body = _workflow_body(
            name=name, model=model, agent_id=agent_id, description=description,
            allowed_models=allowed_models, monthly_budget_usd=monthly_budget_usd,
        )
        return await self._t.request("POST", f"/v1/workflows/{seg(slug)}/steps", json_body=body)

    async def remove_step(self, slug: str, step: str) -> Dict[str, Any]:
        return await self._t.request("DELETE", f"/v1/workflows/{seg(slug)}/steps/{seg(step)}")

    async def add_edge(
        self, slug: str, *, from_step: str, to_step: str, kind: Literal["sequence", "task"]
    ) -> Dict[str, Any]:
        return await self._t.request(
            "POST", f"/v1/workflows/{seg(slug)}/edges", json_body={"from": from_step, "to": to_step, "kind": kind}
        )

    async def remove_edge(self, slug: str, edge_id: str, *, revoke_binding: bool = False) -> Dict[str, Any]:
        return await self._t.request(
            "DELETE", f"/v1/workflows/{seg(slug)}/edges/{seg(edge_id)}",
            params={"revoke_binding": "true"} if revoke_binding else None,
        )


class AsyncWorkflowStep:
    """See :class:`forgebench._resources.WorkflowStep`; ``stream=True`` returns an :class:`AsyncChatStream`."""

    def __init__(self, run: "AsyncWorkflowRun", name: str) -> None:
        self._run = run
        self.name = name

    async def chat(
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
    ) -> Union[ChatCompletion, AsyncChatStream]:
        run = self._run
        payload, headers = _step_request(
            run._keys, self.name, run.last_call_id, messages=messages, model=model, stream=stream,
            temperature=temperature, max_tokens=max_tokens, trace_id=trace_id, extra_body=extra_body,
            metadata=metadata, session_id=session_id, user=user, tags=tags, dimensions=dimensions,
        )
        if stream:
            out: Union[ChatCompletion, AsyncChatStream] = AsyncChatStream(run._t, payload, headers)
        else:
            out = ChatCompletion.from_dict(
                await run._t.request("POST", "/v1/chat/completions", json_body=payload, headers=headers)
            )
        run._calls.append(out)
        return out


class AsyncWorkflowRun:
    """``async with wf.run() as run: await run.step("grading").chat(...)``; see :class:`forgebench._resources.WorkflowRun`."""

    def __init__(self, transport: AsyncTransport, slug: str, keys: Mapping[str, str]) -> None:
        self._t = transport
        self.slug = slug
        self._keys = keys
        self._calls: List[Any] = []

    def step(self, name: str) -> AsyncWorkflowStep:
        if name not in self._keys:
            raise ValueError(f"workflow {self.slug!r}: no api key for step {name!r} (keys: {', '.join(self._keys)})")
        return AsyncWorkflowStep(self, name)

    @property
    def call_ids(self) -> List[str]:
        return [c.call_id for c in self._calls if c.call_id]

    @property
    def last_call_id(self) -> Optional[str]:
        """The newest call's id that is known: a call without one (an old server, a
        stream not read yet) does not break the chain."""
        return next((c.call_id for c in reversed(self._calls) if c.call_id), None)

    @property
    def root_call_id(self) -> Optional[str]:
        return self._calls[0].call_id if self._calls else None

    async def __aenter__(self) -> "AsyncWorkflowRun":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class AsyncWorkflow:
    def __init__(self, transport: AsyncTransport, slug: str, keys: Mapping[str, str]) -> None:
        self._t = transport
        self.slug = slug
        self._keys = _check_keys(slug, keys)

    @property
    def steps(self) -> List[str]:
        return list(self._keys)

    def run(self) -> AsyncWorkflowRun:
        return AsyncWorkflowRun(self._t, self.slug, self._keys)
