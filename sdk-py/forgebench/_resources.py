"""Synchronous API resource groups: chat, agents, runs.

These map 1:1 onto the control plane's governed endpoints:

  chat.completions.create  -> POST /v1/chat/completions  (sync + SSE stream)
  agents.list              -> GET  /v1/agents
  agents.create            -> POST /v1/agents
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
import time
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Union

from ._trace import PARENT_CALL_HEADER
from ._transport import SyncTransport
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


def _chat_payload(
    *,
    model: str,
    messages: List[MessageLike],
    stream: bool,
    temperature: Optional[float],
    max_tokens: Optional[int],
    trace_id: Optional[str],
    extra: Optional[Mapping[str, Any]],
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
        # The control plane's chokepoint accepts a caller-supplied trace_id
        # and stamps it on the audit/metering row and the Langfuse trace
        # (app.routers.chat.run_governed_call). Passing the SAME id here and
        # threading it into the agent's own MCP client calls that follow is
        # what lets a tool call nest under the model call that triggered it
        # in the trace view, instead of showing up as an unrelated span —
        # see docs/MCP-MVP1-PLAN.md's note on trace correlation (G7).
        payload["trace_id"] = trace_id
    if extra:
        # NOTE: the chokepoint uses extra='ignore' and strips api_key/api_base,
        # so these never leak to the provider; passthrough is safe + harmless.
        for k, v in extra.items():
            payload.setdefault(k, v)
    return payload


class Completions:
    def __init__(self, transport: SyncTransport) -> None:
        self._t = transport

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
    ) -> Union[ChatCompletion, Iterator[ChatCompletionChunk]]:
        """Create a chat completion through the governed chokepoint.

        With ``stream=False`` returns a :class:`ChatCompletion`. With
        ``stream=True`` returns an iterator of :class:`ChatCompletionChunk`
        (OpenAI-shaped SSE deltas).

        ``trace_id``: pass the SAME value to this call and thread it into the
        agent's own MCP client calls the model's response leads to, so the
        tool calls nest under this model call in the trace view rather than
        each starting their own unrelated trace. Generate one with
        :func:`forgebench.new_trace_id` at the start of a turn if you don't
        already have a correlation id of your own. Optional — omit for the
        server to mint one, in which case no client-side correlation is
        possible for any tool calls that follow.

        ``parent_call_id``: the ``call_id`` of the governed call that CAUSED
        this one — the orchestrating turn whose answer led here, or the
        previous turn of a tool loop. The server records this call as its
        child, so the ledger holds the workflow as a tree (cost rolls up to
        the root, the console draws who called what) instead of a flat set
        of rows sharing a trace. Where ``trace_id`` groups, this structures.
        Read it off the previous response's :attr:`ChatCompletion.call_id`.
        Optional — omit and this call is a root. Never affects whether the
        call is allowed or what it costs.
        """
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
        data = self._t.request(
            "POST", "/v1/chat/completions", json_body=payload, headers=headers
        )
        return ChatCompletion.from_dict(data)

    def _stream(
        self, payload: Dict[str, Any], headers: Optional[Mapping[str, str]] = None
    ) -> Iterator[ChatCompletionChunk]:
        for event in self._t.stream_sse(
            "POST", "/v1/chat/completions", json_body=payload, headers=headers
        ):
            yield ChatCompletionChunk.from_dict(event)


class Chat:
    def __init__(self, transport: SyncTransport) -> None:
        self.completions = Completions(transport)


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
        data = self._t.request("POST", "/v1/agents", json_body=body)
        return Agent.from_dict(data)

    def run(
        self,
        agent_id: str,
        *,
        input: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        wait: bool = False,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
    ) -> Run:
        """Run an agent.

        Agents execute through the runs subsystem (``POST /v1/runs`` with an
        ``agent_id``), which routes back through the same governed chokepoint
        (budget re-gated per model hop, metered, audited). With ``wait=True``
        the call blocks, polling until the run reaches a terminal state.
        """
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
            f"/v1/agents/{callee}/tasks",
            json_body=body,
            params={"wait": wait},
            headers={PARENT_CALL_HEADER: parent_call_id},
        )
        return Task.from_dict(resp)

    def task(self, callee: str, task_id: str, *, wait: float = 0.0) -> Task:
        """Read a task you opened or were asked to do (A2A ``GetTask``)."""
        resp = self._t.request("GET", f"/v1/agents/{callee}/tasks/{task_id}", params={"wait": wait})
        return Task.from_dict(resp)

    def cancel(self, callee: str, task_id: str) -> Task:
        """Cancel a task you opened (A2A ``CancelTask``). Idempotent."""
        return Task.from_dict(self._t.request("POST", f"/v1/agents/{callee}/tasks/{task_id}/cancel"))

    # --- The callee side: pull delivery --------------------------------------
    def next_task(self, *, wait: float = 20.0) -> Optional[Task]:
        """Claim the next task opened on THIS agent, long-polling up to
        ``wait`` seconds. None when there is none. The claim is exclusive:
        N replicas polling at once each get a different task."""
        resp = self._t.request("GET", "/v1/agents/me/tasks/next", params={"wait": wait})
        return Task.from_dict(resp) if resp else None

    def reply(self, task_id: str, reply: TaskReply) -> Task:
        """Finish, pause, or fail a task this agent claimed."""
        resp = self._t.request("POST", f"/v1/agents/me/tasks/{task_id}/result", json_body=reply.to_dict())
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
        data = self._t.request("GET", f"/v1/runs/{run_id}")
        return Run.from_dict(data)

    def wait(
        self,
        run_id: str,
        *,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
    ) -> Run:
        return _wait_run(self._t, run_id, timeout=timeout, poll_interval=poll_interval)


def _wait_run(
    transport: SyncTransport,
    run_id: str,
    *,
    timeout: float,
    poll_interval: float,
) -> Run:
    deadline = time.monotonic() + timeout
    while True:
        data = transport.request("GET", f"/v1/runs/{run_id}")
        run = Run.from_dict(data)
        if run.is_terminal:
            return run
        if time.monotonic() >= deadline:
            from ._exceptions import ForgebenchError

            raise ForgebenchError(
                f"run {run_id} did not reach a terminal state within {timeout}s "
                f"(last status: {run.status})"
            )
        time.sleep(poll_interval)


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
        loop — the model gets to decide what to do about a failed tool.
        """
        messages: List[Dict[str, Any]] = []
        for tc in tool_calls or []:
            tc_id, name, arguments = _parse_tool_call(tc)
            started = time.monotonic()
            try:
                result = execute(name, arguments)
                latency = int((time.monotonic() - started) * 1000)
                self.report(call_id=call_id, tool_name=name, result=result, latency_ms=latency)
                content: Any = result
            except Exception as exc:  # noqa: BLE001 - the model decides what a failed tool means
                latency = int((time.monotonic() - started) * 1000)
                self.report(call_id=call_id, tool_name=name, error=str(exc)[:4000], latency_ms=latency)
                content = {"error": str(exc)}
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
