from __future__ import annotations

import atexit
import contextvars
import functools
import hashlib
import inspect
import logging
import random
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, TypeVar

from .._trace import new_trace_id, validate_trace_id
from ..version import __version__
from ._config import TracingConfig
from ._exporter import BatchExporter
from ._observation import OBSERVATION_TYPES, Observation, ObservationType, TraceState
from ._serialize import capped, to_jsonable

logger = logging.getLogger("forgebench.tracing")

BATCH_PATH = "/v1/observations/batch"
_SDK = {"name": "forgebench-python", "version": __version__}
_MAX_STATUS_CHARS = 1_000

F = TypeVar("F", bound=Callable[..., Any])

_current: contextvars.ContextVar[Optional[Observation]] = contextvars.ContextVar(
    "forgebench_current_observation", default=None
)

# The tracer module-level ``observe`` uses to START a trace, worked out from the
# clients that are OPEN right now, by credential. One credential: its newest
# open client. Several: ambiguous — guessing could send one workspace's steps
# with another's key — so module-level tracing refuses to start traces and
# asks for a client-bound ``client.observe``. Closing a client takes it out,
# so the answer follows the clients actually in use.
_live: Dict[str, List["Tracer"]] = {}
_live_lock = threading.Lock()


def current_observation() -> Optional[Observation]:
    """The observation the running code is inside of, if any."""
    return _current.get()


def register_default_tracer(tracer: "Tracer", identity: str) -> None:
    """Count ``tracer`` as an open client. ``identity`` names its credential (a
    hash of base URL + key); clients sharing one are interchangeable."""
    with _live_lock:
        _live.setdefault(identity, []).append(tracer)


def release_default_tracer(tracer: "Tracer") -> None:
    """``tracer``'s client is closed: it no longer counts."""
    with _live_lock:
        for identity, tracers in list(_live.items()):
            if tracer in tracers:
                tracers.remove(tracer)
            if not tracers:
                del _live[identity]


def _default_tracer() -> Tuple[Optional["Tracer"], bool]:
    """``(tracer, ambiguous)`` for module-level functions outside any trace."""
    with _live_lock:
        if len(_live) > 1:
            return next(iter(_live.values()))[-1], True
        if _live:
            return next(iter(_live.values()))[-1], False
        return None, False


def resolve_tracer() -> Optional["Tracer"]:
    current = _current.get()
    if current is not None:
        return current.trace.tracer
    tracer, ambiguous = _default_tracer()
    if ambiguous:
        tracer._warn_once(  # type: ignore[union-attr]
            "ambiguous-default",
            "forgebench tracing: open clients for several API keys exist, so module-level "
            "observe() does not start traces; use client.observe(...) instead",
        )
        return None
    return tracer


class Tracer:
    """Records observations for one client and sends them in the background."""

    def __init__(self, config: TracingConfig, send_batch: Callable[[Dict[str, Any]], Any]) -> None:
        self.config = config
        self._send_batch = send_batch
        self._exporter: Optional[BatchExporter] = None
        self._lock = threading.Lock()
        self._warned: set = set()

    # -- starting observations ---------------------------------------------
    def start_observation(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        parent: Optional[Observation] = None,
    ) -> Observation:
        """A new observation under ``parent`` (default: the current one), or the
        root of a new trace. ``trace_id`` starts a root in that trace instead —
        to continue a trace begun elsewhere."""
        if as_type not in OBSERVATION_TYPES:
            raise ValueError(f"as_type must be one of {sorted(OBSERVATION_TYPES)}, got {as_type!r}")
        if trace_id is None and parent is None:
            current = _current.get()
            # Steps of ANOTHER client's trace are not nested under: each client
            # only ever sends its own traces, with its own key.
            parent = current if current is not None and current.trace.tracer is self else None
        if trace_id is None and parent is not None:
            trace = parent.trace
        else:
            trace = TraceState(validate_trace_id(trace_id) or new_trace_id(), sampled=self._sample(), tracer=self)
            parent = None
        observation = Observation(
            trace=trace, parent_id=parent.id if parent else None, as_type=as_type, name=str(name)
        )
        if input is not None:
            observation.update(input=input)
        if metadata:
            observation.update(metadata=metadata)
        return observation

    @contextmanager
    def start_as_current_observation(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> Iterator[Observation]:
        """Context manager: the observation is current inside the block, so
        observations and model calls made there nest under it. An exception
        is recorded as ``level="ERROR"`` and re-raised."""
        observation = self.start_observation(
            name, as_type=as_type, input=input, metadata=metadata, trace_id=trace_id
        )
        token = _current.set(observation)
        try:
            yield observation
        except Exception as exc:
            observation.update(level="ERROR", status_message=_describe(exc))
            raise
        finally:
            _current.reset(token)
            observation.end()

    def observe(
        self,
        fn: Optional[F] = None,
        *,
        name: Optional[str] = None,
        as_type: ObservationType = "span",
        capture_input: bool = True,
        capture_output: bool = True,
    ) -> Any:
        """Decorator recording each call of a function as an observation. See
        :func:`forgebench.tracing.observe`."""
        return make_observe(lambda: self)(
            fn, name=name, as_type=as_type, capture_input=capture_input, capture_output=capture_output
        )

    # -- updating ------------------------------------------------------------
    def update_current_observation(self, **fields: Any) -> None:
        current = self._current_or_warn("update_current_observation")
        if current is not None:
            current.update(**fields)

    def update_current_trace(
        self,
        *,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        current = self._current_or_warn("update_current_trace")
        if current is None or not current.recording:
            return
        current.trace.update(
            name=name,
            user_id=user_id,
            session_id=session_id,
            tags=list(tags) if tags else None,
            metadata=self._prepare_mapping(metadata) if metadata else None,
        )

    def _prepare_mapping(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """:meth:`prepare` for metadata, which must stay a mapping: a value the
        mask or the size cap turned into something else is kept under one key,
        never merged — tracing must not raise into the agent."""
        prepared = self.prepare(metadata)
        return prepared if isinstance(prepared, dict) else {"value": prepared}

    # -- ids -----------------------------------------------------------------
    @staticmethod
    def create_trace_id(seed: Optional[str] = None) -> str:
        """A new 32-hex trace id; with ``seed`` (e.g. your request id) always the same one."""
        if seed is None:
            return new_trace_id()
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

    # -- sending -------------------------------------------------------------
    def prepare(self, value: Any) -> Any:
        """Serialise, mask and size-cap one input / output / metadata value."""
        data = to_jsonable(value)
        mask = self.config.mask
        if mask is not None:
            try:
                data = to_jsonable(mask(data))
            except Exception as exc:  # noqa: BLE001 - never send what the mask was meant to hide
                self._warn_once("mask", "forgebench tracing: mask failed, value withheld: %s", exc)
                data = "[withheld: mask failed]"
        return capped(data)

    def prepare_text(self, text: Optional[str]) -> Optional[str]:
        """A status message through the mask (error messages often carry the
        very values the mask exists for), cut to the server's limit."""
        if text is None:
            return None
        if self.config.mask is not None:
            try:
                masked = self.config.mask(text)
                text = masked if isinstance(masked, str) else str(masked)
            except Exception as exc:  # noqa: BLE001 - never send what the mask was meant to hide
                self._warn_once("mask", "forgebench tracing: mask failed, value withheld: %s", exc)
                return "[withheld: mask failed]"
        return text[:_MAX_STATUS_CHARS]

    def finish(self, observation: Observation) -> None:
        """Queue an ended observation (and, when due, its trace record)."""
        if not observation.recording:
            return
        trace = observation.trace.take_record(
            force=observation.parent_id is None,
            defaults={
                "name": observation.name,
                "environment": self.config.environment,
                "release": self.config.release,
            },
        )
        self._exporter_or_start().add(observation.to_wire(), trace)

    def flush(self, timeout: float = 5.0) -> bool:
        exporter = self._exporter
        return exporter.flush(timeout) if exporter is not None else True

    def shutdown(self, timeout: float = 5.0) -> None:
        release_default_tracer(self)
        exporter = self._exporter
        if exporter is not None:
            exporter.shutdown(timeout)

    # -- internals -----------------------------------------------------------
    def _sample(self) -> bool:
        return self.config.enabled and random.random() < self.config.sample_rate

    def _exporter_or_start(self) -> BatchExporter:
        with self._lock:
            if self._exporter is None:
                self._exporter = BatchExporter(
                    self._send,
                    flush_at=self.config.flush_at,
                    flush_interval=self.config.flush_interval,
                    max_queue_size=self.config.max_queue_size,
                )
                atexit.register(self._exporter.shutdown)
            return self._exporter

    def _send(self, observations: List[Dict[str, Any]], traces: List[Dict[str, Any]]) -> None:
        reply = self._send_batch({"observations": observations, "traces": traces, "sdk": _SDK}) or {}
        for rejection in reply.get("rejected") or []:
            reason = rejection.get("reason")
            self._warn_once(
                f"rejected:{reason}",
                "forgebench tracing: the server rejected observation %s (%s)",
                rejection.get("id"),
                reason,
            )
        dropped = reply.get("dropped_reason")
        if dropped:
            self._warn_once(f"dropped:{dropped}", "forgebench tracing: traces are not stored (%s)", dropped)

    def _current_or_warn(self, method: str) -> Optional[Observation]:
        current = _current.get()
        if current is None:
            self._warn_once(method, "forgebench tracing: %s() called outside any observation; ignored", method)
        return current

    def _warn_once(self, key: str, message: str, *args: Any) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(message, *args)


def make_observe(resolve: Callable[[], Optional[Tracer]]) -> Callable[..., Any]:
    """Build an ``observe`` decorator whose tracer is looked up at CALL time."""

    def observe(
        fn: Optional[F] = None,
        *,
        name: Optional[str] = None,
        as_type: ObservationType = "span",
        capture_input: bool = True,
        capture_output: bool = True,
    ) -> Any:
        def decorate(func: F) -> F:
            if inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func):
                raise TypeError("observe() does not support generator functions; observe the code that consumes it")
            step_name = name or func.__name__

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    tracer = resolve()
                    if tracer is None:
                        return await func(*args, **kwargs)
                    with tracer.start_as_current_observation(step_name, as_type=as_type) as observation:
                        if capture_input and observation.recording:
                            observation.update(input=_arguments(func, args, kwargs))
                        result = await func(*args, **kwargs)
                        if capture_output and observation.recording:
                            observation.update(output=result)
                        return result

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = resolve()
                if tracer is None:
                    return func(*args, **kwargs)
                with tracer.start_as_current_observation(step_name, as_type=as_type) as observation:
                    if capture_input and observation.recording:
                        observation.update(input=_arguments(func, args, kwargs))
                    result = func(*args, **kwargs)
                    if capture_output and observation.recording:
                        observation.update(output=result)
                    return result

            return wrapper  # type: ignore[return-value]

        return decorate(fn) if fn is not None else decorate

    return observe


def bind_context(fn: F) -> F:
    """``fn`` running inside the CURRENT observation, wherever it is called —
    for handing work to a thread pool: ``pool.submit(bind_context(work), x)``.
    (``asyncio`` tasks inherit the current observation without this.)"""
    context = contextvars.copy_context()

    @functools.wraps(fn)
    def bound(*args: Any, **kwargs: Any) -> Any:
        return context.copy().run(fn, *args, **kwargs)

    return bound  # type: ignore[return-value]


def _arguments(func: Callable[..., Any], args: tuple, kwargs: dict) -> Dict[str, Any]:
    """The call's arguments by parameter name, without ``self`` / ``cls``."""
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        return {"args": list(args), "kwargs": kwargs}
    arguments = dict(bound.arguments)
    for implicit in ("self", "cls"):
        arguments.pop(implicit, None)
    return arguments


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:_MAX_STATUS_CHARS]
