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
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

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


def _resolve_api_key(api_key: Optional[str]) -> Optional[str]:
    return api_key if api_key is not None else os.environ.get("FORGEBENCH_API_KEY")


def _resolve_base_url(base_url: Optional[str]) -> str:
    if base_url:
        return base_url
    return os.environ.get("FORGEBENCH_BASE_URL") or DEFAULT_BASE_URL


class Forgebench:
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
    ) -> None:
        self._transport = SyncTransport(
            api_key=_resolve_api_key(api_key),
            base_url=_resolve_base_url(base_url),
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            http_client=http_client,
        )
        self.chat = Chat(self._transport)
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

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "Forgebench":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class AsyncForgebench:
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
    ) -> None:
        self._transport = AsyncTransport(
            api_key=_resolve_api_key(api_key),
            base_url=_resolve_base_url(base_url),
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            http_client=http_client,
        )
        self.chat = AsyncChat(self._transport)
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

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> "AsyncForgebench":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
