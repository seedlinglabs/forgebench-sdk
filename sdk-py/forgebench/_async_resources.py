"""Asynchronous mirror of :mod:`forgebench._resources` (chat, agents, runs).

Same endpoint mapping and semantics as the sync resources; methods are
``async`` and streaming yields via ``async for``.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Mapping, Optional, Union

from ._resources import (
    MessageLike,
    _chat_payload,
    _coerce_reply,
    _parse_tool_call,
    _tool_message,
)
from ._trace import PARENT_CALL_HEADER
from ._transport import AsyncTransport
from .types import (
    Agent,
    ChatCompletion,
    ChatCompletionChunk,
    MeteringSummary,
    Run,
    Task,
    TaskReply,
    ToolBinding,
    ToolOutcomeReceipt,
    WhoAmI,
    message_parts,
)


class AsyncCompletions:
    def __init__(self, transport: AsyncTransport) -> None:
        self._t = transport

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
    ) -> Union[ChatCompletion, AsyncIterator[ChatCompletionChunk]]:
        """See :meth:`forgebench._resources.Completions.create` — identical
        semantics, including the ``trace_id`` correlation contract and the
        ``parent_call_id`` lineage contract."""
        payload = _chat_payload(
            model=model,
            messages=messages,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
            trace_id=trace_id,
            extra=extra_body,
        )
        headers = {PARENT_CALL_HEADER: parent_call_id}
        if stream:
            return self._stream(payload, headers)
        data = await self._t.request(
            "POST", "/v1/chat/completions", json_body=payload, headers=headers
        )
        return ChatCompletion.from_dict(data)

    async def _stream(
        self, payload: Dict[str, Any], headers: Optional[Mapping[str, str]] = None
    ) -> AsyncIterator[ChatCompletionChunk]:
        async for event in self._t.stream_sse(
            "POST", "/v1/chat/completions", json_body=payload, headers=headers
        ):
            yield ChatCompletionChunk.from_dict(event)


class AsyncChat:
    def __init__(self, transport: AsyncTransport) -> None:
        self.completions = AsyncCompletions(transport)


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
        data = await self._t.request("POST", "/v1/agents", json_body=body)
        return Agent.from_dict(data)

    async def run(
        self,
        agent_id: str,
        *,
        input: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        wait: bool = False,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
    ) -> Run:
        body: Dict[str, Any] = {"input": input or {}}
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
            f"/v1/agents/{callee}/tasks",
            json_body=body,
            params={"wait": wait},
            headers={PARENT_CALL_HEADER: parent_call_id},
        )
        return Task.from_dict(resp)

    async def task(self, callee: str, task_id: str, *, wait: float = 0.0) -> Task:
        resp = await self._t.request("GET", f"/v1/agents/{callee}/tasks/{task_id}", params={"wait": wait})
        return Task.from_dict(resp)

    async def cancel(self, callee: str, task_id: str) -> Task:
        return Task.from_dict(await self._t.request("POST", f"/v1/agents/{callee}/tasks/{task_id}/cancel"))

    async def next_task(self, *, wait: float = 20.0) -> Optional[Task]:
        resp = await self._t.request("GET", "/v1/agents/me/tasks/next", params={"wait": wait})
        return Task.from_dict(resp) if resp else None

    async def reply(self, task_id: str, reply: TaskReply) -> Task:
        resp = await self._t.request(
            "POST", f"/v1/agents/me/tasks/{task_id}/result", json_body=reply.to_dict()
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
        data = await self._t.request("GET", f"/v1/runs/{run_id}")
        return Run.from_dict(data)

    async def wait(
        self,
        run_id: str,
        *,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
    ) -> Run:
        return await _await_run(self._t, run_id, timeout=timeout, poll_interval=poll_interval)


async def _await_run(
    transport: AsyncTransport,
    run_id: str,
    *,
    timeout: float,
    poll_interval: float,
) -> Run:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        data = await transport.request("GET", f"/v1/runs/{run_id}")
        run = Run.from_dict(data)
        if run.is_terminal:
            return run
        if loop.time() >= deadline:
            from ._exceptions import ForgebenchError

            raise ForgebenchError(
                f"run {run_id} did not reach a terminal state within {timeout}s "
                f"(last status: {run.status})"
            )
        await asyncio.sleep(poll_interval)


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
            try:
                result = execute(name, arguments)
                if inspect.isawaitable(result):
                    result = await result
                latency = int((time.monotonic() - started) * 1000)
                await self.report(call_id=call_id, tool_name=name, result=result, latency_ms=latency)
                content: Any = result
            except Exception as exc:  # noqa: BLE001 - the model decides what a failed tool means
                latency = int((time.monotonic() - started) * 1000)
                await self.report(
                    call_id=call_id, tool_name=name, error=str(exc)[:4000], latency_ms=latency
                )
                content = {"error": str(exc)}
            messages.append(_tool_message(tc_id, name, content))
        return messages
