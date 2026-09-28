"""Typed exception hierarchy for the Forgebench SDK.

Every non-2xx response from the control plane is surfaced as a typed error so
callers can branch on, e.g., a budget trip (``BudgetExceededError`` / 402) vs an
auth failure (``AuthenticationError`` / 401) without string-matching messages.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx


class ForgebenchError(Exception):
    """Base class for every error raised by the SDK."""


class ForgebenchConnectionError(ForgebenchError):
    """Network-level failure talking to the control plane (DNS, refused, timeout)."""

    def __init__(self, message: str, *, cause: Optional[BaseException] = None) -> None:
        super().__init__(message)
        self.__cause__ = cause


class APIError(ForgebenchError):
    """Non-2xx HTTP response from the control plane.

    Mirrors the control plane's ``ErrorResponse`` shape
    (``{"detail": str, "code": str | None}``) when present.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: Optional[str] = None,
        body: Any = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.body = body
        self.request_id = request_id

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        base = f"[{self.status_code}] {self.message}"
        if self.code:
            base += f" (code={self.code})"
        if self.request_id:
            base += f" (request_id={self.request_id})"
        return base


class AuthenticationError(APIError):
    """401 — missing/invalid/revoked API key or token."""


class PermissionDeniedError(APIError):
    """403 — authenticated but lacking the required role/scope."""


class NotFoundError(APIError):
    """404 — the resource does not exist (or is not visible to this tenant)."""


class BudgetExceededError(APIError):
    """402 — the pre-call budget gate fired before any provider call.

    This is the SDK surface of the governed chokepoint's budget gate: the
    tenant's monthly spend cap was reached, so the request was rejected *before*
    reaching the model gateway. No tokens were spent and nothing was metered.
    """


class RateLimitError(APIError):
    """429 — too many requests."""


class ConflictError(APIError):
    """409 — conflicting state (e.g. duplicate)."""


class ValidationError(APIError):
    """422 — request body failed control-plane validation."""


class ServerError(APIError):
    """5xx — the control plane errored."""


_STATUS_TO_EXC = {
    401: AuthenticationError,
    402: BudgetExceededError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def raise_for_response(response: httpx.Response) -> None:
    """Raise the most specific :class:`APIError` for a non-2xx response.

    2xx responses return without raising.
    """
    if response.is_success:
        return

    code: Optional[str] = None
    detail: Optional[str] = None
    body: Any
    try:
        body = response.json()
        if isinstance(body, dict):
            # Two response shapes reach this code:
            #   generic ErrorResponse:  {"detail": str, "code": str | None}
            #   structured error body:  {"detail": {"message": ..., "code": ...}}
            # (FastAPI's HTTPException always nests the raise's `detail=` payload
            # under a top-level "detail" key — a dict passed to `detail=` is never
            # hoisted to the top level, so `code` must be read from wherever the
            # raiser actually put it: top-level for the generic shape, nested for
            # the structured one.)
            raw_detail = body.get("detail")
            if isinstance(raw_detail, dict):
                detail = str(raw_detail.get("message") or raw_detail)
                c = raw_detail.get("code")
            else:
                detail = str(raw_detail) if raw_detail is not None else None
                c = body.get("code")
            code = str(c) if c is not None else None
    except ValueError:
        body = response.text or None
        detail = body if isinstance(body, str) else None

    message = detail or response.reason_phrase or f"HTTP {response.status_code}"
    request_id = response.headers.get("x-request-id")

    status = response.status_code
    exc_cls = _STATUS_TO_EXC.get(status)
    if exc_cls is None:
        exc_cls = ServerError if status >= 500 else APIError

    raise exc_cls(
        message,
        status_code=status,
        code=code,
        body=body,
        request_id=request_id,
    )
