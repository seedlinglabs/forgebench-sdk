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
from ._resources import ChatStream
from ._trace import AUDIO_TRACE_ID_HEADER, PARENT_CALL_HEADER, SESSION_HEADER, TRACE_ID_HEADER, TURN_HEADER, new_trace_id, voice_headers
from .tracing import Observation, TracingConfig, bind_context, observe
from ._exceptions import (
    APIError,
    AuthenticationError,
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    StreamError,
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
    SpeechAudio,
    Task,
    TaskReply,
    ToolBinding,
    ToolOutcomeReceipt,
    Transcription,
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
    "voice_headers",
    "TURN_HEADER",
    "PARENT_CALL_HEADER",
    "SESSION_HEADER",
    "TRACE_ID_HEADER",
    # tracing
    "observe",
    "bind_context",
    "Observation",
    "TracingConfig",
    # streams
    "ChatStream",
    "AsyncChatStream",
    # types
    "Agent",
    "Run",
    "Transcription",
    "SpeechAudio",
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
    "StreamError",
    "AUDIO_TRACE_ID_HEADER",
]
