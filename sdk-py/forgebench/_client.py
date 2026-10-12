"""The public client objects: :class:`Forgebench` (sync) and :class:`AsyncForgebench`.

Usage
-----
>>> from forgebench import Forgebench
>>> client = Forgebench(api_key="sk_...")
>>> resp = client.chat.completions.create(
...     model="mock-gpt",
...     messages=[{"role": "user", "content": "Prove the governed path works."}],
... )
>>> print(resp.choices[0].message.content)

The ``api_key`` defaults to the ``FORGEBENCH_API_KEY`` environment variable and
``base_url`` to ``FORGEBENCH_BASE_URL`` (falling back to the production control
plane at https://api.forgebench.ai), so scripts need no arguments in
production and only ``base_url="http://localhost:8000"`` for local dev.

Both clients also carry the tracing API (:mod:`forgebench.tracing`): steps are
recorded with this client's key and sent in the background.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import AbstractContextManager
from typing import Any, Callable, Dict, List, Mapping, Optional

import httpx

from ._async_resources import (
    AsyncAccount,
    AsyncAgents,
    AsyncAgentTools,
    AsyncChat,
    AsyncRuns,
)
from ._resources import Account, AgentTools, Agents, Chat, Runs
from ._transport import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    AsyncTransport,
    SyncTransport,
)
from .tracing import Observation, ObservationType, TracingConfig
from .tracing._tracer import BATCH_PATH, Tracer, current_observation, register_default_tracer

# Sending steps is bookkeeping: a short timeout, so a slow control plane only
# ever delays the background sender, never the agent.
_TRACING_TIMEOUT = 10.0


def _resolve_api_key(api_key: Optional[str]) -> Optional[str]:
    return api_key if api_key is not None else os.environ.get("FORGEBENCH_API_KEY")


def _resolve_base_url(base_url: Optional[str]) -> str:
    if base_url:
        return base_url
    return os.environ.get("FORGEBENCH_BASE_URL") or DEFAULT_BASE_URL


def _make_tracer(
    config: Optional[TracingConfig],
    *,
    api_key: Optional[str],
    base_url: str,
    default_headers: Optional[Mapping[str, str]],
) -> Tracer:
    """A tracer that sends with its OWN sync transport (it runs on the sender
    thread, for the sync and the async client alike), built on first send."""
    transport: Optional[SyncTransport] = None

    def send_batch(payload: Dict[str, Any]) -> Any:
        nonlocal transport
        if transport is None:
            transport = SyncTransport(
                api_key=api_key,
                base_url=base_url,
                timeout=_TRACING_TIMEOUT,
                max_retries=DEFAULT_MAX_RETRIES,
                default_headers=default_headers,
            )
        return transport.request("POST", BATCH_PATH, json_body=payload)

    tracer = Tracer(config or TracingConfig.from_env(), send_batch)
    if tracer.config.enabled:
        identity = hashlib.sha256(f"{base_url}\n{api_key or ''}".encode("utf-8")).hexdigest()
        register_default_tracer(tracer, identity)
    return tracer


class _Tracing:
    """Tracing methods shared by both clients. See :mod:`forgebench.tracing`."""

    _tracer: Tracer

    def observe(
        self,
        fn: Optional[Callable[..., Any]] = None,
        *,
        name: Optional[str] = None,
        as_type: ObservationType = "span",
        capture_input: bool = True,
        capture_output: bool = True,
    ) -> Any:
        """:func:`forgebench.observe`, recording with THIS client."""
        return self._tracer.observe(
            fn, name=name, as_type=as_type, capture_input=capture_input, capture_output=capture_output
        )

    def start_as_current_observation(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> AbstractContextManager[Observation]:
        """``with client.start_as_current_observation("plan") as step: ...`` —
        a step for a block of code. Steps and model calls inside nest under it;
        an exception is recorded as an error and re-raised."""
        return self._tracer.start_as_current_observation(
            name, as_type=as_type, input=input, metadata=metadata, trace_id=trace_id
        )

    def start_observation(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> Observation:
        """A step that does NOT become current, for work that starts and ends
        in different places. Call ``.end()`` on it, or it is never sent."""
        return self._tracer.start_observation(
            name, as_type=as_type, input=input, metadata=metadata, trace_id=trace_id
        )

    def update_current_observation(
        self,
        *,
        name: Optional[str] = None,
        input: Any = None,
        output: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        level: Optional[str] = None,
        status_message: Optional[str] = None,
    ) -> None:
        """Change the current step: only the fields you pass."""
        fields = {
            "name": name, "input": input, "output": output, "metadata": metadata,
            "level": level, "status_message": status_message,
        }
        self._tracer.update_current_observation(**{k: v for k, v in fields.items() if v is not None})

    def update_current_trace(
        self,
        *,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Set trace-level fields: ``user_id`` (your end user), ``session_id``
        (groups the traces of one conversation), ``tags``, ``metadata``, and
        ``name`` (defaults to the root step's name). Model calls in this trace
        inherit ``user_id`` / ``session_id``."""
        self._tracer.update_current_trace(
            name=name, user_id=user_id, session_id=session_id, tags=tags, metadata=metadata
        )

    def get_current_trace_id(self) -> Optional[str]:
        current = current_observation()
        return current.trace_id if current is not None else None

    def get_current_observation_id(self) -> Optional[str]:
        current = current_observation()
        return current.id if current is not None else None

    @staticmethod
    def create_trace_id(seed: Optional[str] = None) -> str:
        """A new trace id; with ``seed`` (e.g. your request id) always the same one."""
        return Tracer.create_trace_id(seed)


class Forgebench(_Tracing):
    """Synchronous client for the Forgebench control plane."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
        tracing: Optional[TracingConfig] = None,
    ) -> None:
        api_key = _resolve_api_key(api_key)
        base_url = _resolve_base_url(base_url)
        self._transport = SyncTransport(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            http_client=http_client,
        )
        self._tracer = _make_tracer(tracing, api_key=api_key, base_url=base_url, default_headers=default_headers)
        self.chat = Chat(self._transport, self._tracer)
        self.agents = Agents(self._transport)
        self.runs = Runs(self._transport)
        self.agent_tools = AgentTools(self._transport)
        self.account = Account(self._transport)

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    def whoami(self):
        """Shortcut for ``client.account.whoami()`` — verify auth + tenant."""
        return self.account.whoami()

    def metering_summary(self):
        """Shortcut for ``client.account.metering_summary()`` — budget state."""
        return self.account.metering_summary()

    def flush(self, timeout: float = 5.0) -> bool:
        """Send every finished step now. Long-running apps never need this;
        call it before a short script or serverless function exits."""
        return self._tracer.flush(timeout)

    def close(self) -> None:
        """Send what is left of the traces, then close connections."""
        self._tracer.shutdown()
        self._transport.close()

    def __enter__(self) -> "Forgebench":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class AsyncForgebench(_Tracing):
    """Asynchronous client for the Forgebench control plane."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        tracing: Optional[TracingConfig] = None,
    ) -> None:
        api_key = _resolve_api_key(api_key)
        base_url = _resolve_base_url(base_url)
        self._transport = AsyncTransport(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            http_client=http_client,
        )
        self._tracer = _make_tracer(tracing, api_key=api_key, base_url=base_url, default_headers=default_headers)
        self.chat = AsyncChat(self._transport, self._tracer)
        self.agents = AsyncAgents(self._transport)
        self.runs = AsyncRuns(self._transport)
        self.agent_tools = AsyncAgentTools(self._transport)
        self.account = AsyncAccount(self._transport)

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    async def whoami(self):
        return await self.account.whoami()

    async def metering_summary(self):
        return await self.account.metering_summary()

    async def flush(self, timeout: float = 5.0) -> bool:
        """See :meth:`Forgebench.flush`; waits off the event loop."""
        return await asyncio.to_thread(self._tracer.flush, timeout)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._tracer.shutdown)
        await self._transport.aclose()

    async def __aenter__(self) -> "AsyncForgebench":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
