/**
 * Typed error hierarchy. Every non-2xx HTTP response is mapped to a subclass of
 * {@link ForgebenchAPIError} so callers can branch on the failure mode — most
 * importantly {@link BudgetExceededError} (HTTP 402), which the governed
 * chokepoint raises BEFORE any provider call when a tenant is over budget.
 */

import type { ErrorBody } from "./types.js";

/** Base class for all SDK-raised errors. */
export class ForgebenchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
    // Restore prototype chain when targeting older runtimes / transpilers.
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Raised when a request fails before/without an HTTP response (network, DNS, abort). */
export class ForgebenchConnectionError extends ForgebenchError {
  readonly cause?: unknown;
  constructor(message: string, cause?: unknown) {
    super(message);
    this.cause = cause;
  }
}

/** Raised when a request exceeds the configured timeout. */
export class ForgebenchTimeoutError extends ForgebenchConnectionError {}

/** Raised for any non-2xx HTTP response from the control plane. */
export class ForgebenchAPIError extends ForgebenchError {
  readonly status: number;
  /** Server-provided machine code, when present (`ErrorResponse.code`). */
  readonly code: string | null;
  /** Best-effort parsed response body. */
  readonly body: ErrorBody | string | null;
  /** Selected response headers (e.g. request-id) for support escalation. */
  readonly requestId: string | null;

  constructor(
    status: number,
    message: string,
    opts: { code?: string | null; body?: ErrorBody | string | null; requestId?: string | null } = {},
  ) {
    super(message);
    this.status = status;
    this.code = opts.code ?? null;
    this.body = opts.body ?? null;
    this.requestId = opts.requestId ?? null;
  }
}

/** 400. */
export class BadRequestError extends ForgebenchAPIError {}
/** 401. Bad/missing/expired credentials. */
export class AuthenticationError extends ForgebenchAPIError {}
/** 403. Authenticated but lacks the required role/scope (RBAC). */
export class PermissionDeniedError extends ForgebenchAPIError {}
/** 404. */
export class NotFoundError extends ForgebenchAPIError {}
/** 409. */
export class ConflictError extends ForgebenchAPIError {}
/**
 * 402. The tenant is over its budget. The governed chokepoint returns this
 * BEFORE making any provider call, so no spend occurs on a rejected request.
 */
export class BudgetExceededError extends ForgebenchAPIError {}
/** 422. Request body failed server-side validation. */
export class UnprocessableEntityError extends ForgebenchAPIError {}
/** 429. */
export class RateLimitError extends ForgebenchAPIError {}
/** 5xx. */
export class InternalServerError extends ForgebenchAPIError {}

/** Map an HTTP status + parsed body into the most specific error subclass. */
export function errorFromResponse(
  status: number,
  body: ErrorBody | string | null,
  requestId: string | null,
): ForgebenchAPIError {
  // Two response shapes reach this code: the generic ErrorResponse
  // ({detail: string, code: string | null}) and a structured error body,
  // where FastAPI nests the raise's `detail=` dict under "detail" rather than
  // hoisting its "code" key to the top level ({detail: {message, code}}).
  const nestedDetail =
    body && typeof body === "object" && body.detail && typeof body.detail === "object"
      ? (body.detail as Record<string, unknown>)
      : null;
  const detail =
    typeof body === "string"
      ? body
      : nestedDetail
        ? typeof nestedDetail.message === "string"
          ? nestedDetail.message
          : undefined
        : body && typeof body.detail === "string"
          ? body.detail
          : undefined;
  const code = nestedDetail
    ? typeof nestedDetail.code === "string"
      ? nestedDetail.code
      : null
    : typeof body === "object" && body && typeof body.code === "string"
      ? body.code
      : null;
  const message = detail ? `HTTP ${status}: ${detail}` : `HTTP ${status}`;
  const opts = { code, body, requestId };

  switch (status) {
    case 400:
      return new BadRequestError(status, message, opts);
    case 401:
      return new AuthenticationError(status, message, opts);
    case 402:
      return new BudgetExceededError(status, message, opts);
    case 403:
      return new PermissionDeniedError(status, message, opts);
    case 404:
      return new NotFoundError(status, message, opts);
    case 409:
      return new ConflictError(status, message, opts);
    case 422:
      return new UnprocessableEntityError(status, message, opts);
    case 429:
      return new RateLimitError(status, message, opts);
    default:
      if (status >= 500) return new InternalServerError(status, message, opts);
      return new ForgebenchAPIError(status, message, opts);
  }
}
