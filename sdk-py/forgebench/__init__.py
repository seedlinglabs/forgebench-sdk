"""Official Python SDK for Forgebench.

A thin, dependency-light (httpx-only) client for Forgebench's control-plane
api-key (``sk_...``) surface — the permanent programmatic path. Every call rides
the governed chokepoint: authenticated, tenant-isolated (Postgres RLS),
budget-gated *before* any provider call, then metered and hash-chain audited.

Quick start
-----------
>>> from forgebench import Forgebench
>>> client = Forgebench(api_key="sk_...", base_url="http://localhost:8000")
>>> r = client.chat.completions.create(
...     model="mock-gpt",
...     messages=[{"role": "user", "content": "hello"}],
... )
>>> print(r.choices[0].message.content)
"""

from __future__ import annotations

from ._client import AsyncForgebench, Forgebench
from ._async_resources import AsyncChatStream
from ._langfuse import Langfuse
from ._resources import ChatStream
from ._trace import PARENT_CALL_HEADER, new_trace_id
from ._exceptions import (
    APIError,
    AuthenticationError,
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ForgebenchConnectionError,
    ForgebenchError,
    ValidationError,
)
from .types import (
    Agent,
    ChatChoice,
    ChatChoiceMessage,
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    MeteringSummary,
    Run,
    Task,
    TaskReply,
    ToolBinding,
    ToolOutcomeReceipt,
    Usage,
    WhoAmI,
    message_parts,
)
from .version import __version__

__all__ = [
    "__version__",
    # clients
    "Forgebench",
    "AsyncForgebench",
    # correlation
    "new_trace_id",
    "PARENT_CALL_HEADER",
    # streams / passthrough
    "ChatStream",
    "AsyncChatStream",
    "Langfuse",
    # types
    "Agent",
    "Run",
    "ToolBinding",
    "ToolOutcomeReceipt",
    "Task",
    "TaskReply",
    "message_parts",
    "ChatCompletion",
    "ChatChoice",
    "ChatChoiceMessage",
    "ChatCompletionChunk",
    "ChatCompletionChunkChoice",
    "ChatCompletionChunkDelta",
    "Usage",
    "MeteringSummary",
    "WhoAmI",
    # errors
    "ForgebenchError",
    "ForgebenchConnectionError",
    "APIError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "BudgetExceededError",
    "RateLimitError",
    "ConflictError",
    "ValidationError",
    "ServerError",
]
