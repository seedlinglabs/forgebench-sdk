from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger("forgebench.tracing")


@dataclass(frozen=True)
class TracingConfig:
    """How a client records and sends traces.

    ``Forgebench()`` without a ``tracing=`` argument uses :meth:`from_env`; a
    config passed explicitly is used as-is.

    * ``enabled`` — off makes every tracing call a no-op; model calls still work.
    * ``sample_rate`` — share of traces kept (0.0–1.0), decided once per trace,
      so a trace is sent whole or not at all.
    * ``environment`` / ``release`` — stamped on every trace, for filtering.
    * ``mask`` — ``(data) -> data``, run on every input, output and metadata
      before it leaves the process. Use it to strip what must never be sent.
    * ``flush_at`` / ``flush_interval`` — a batch is sent once this many steps
      are waiting, or this many seconds after the first one.
    * ``max_queue_size`` — beyond it the oldest waiting steps are dropped.
    """

    enabled: bool = True
    sample_rate: float = 1.0
    environment: Optional[str] = None
    release: Optional[str] = None
    mask: Optional[Callable[[Any], Any]] = None
    flush_at: int = 50
    flush_interval: float = 1.0
    max_queue_size: int = 10_000

    def __post_init__(self) -> None:
        if not 0.0 <= self.sample_rate <= 1.0:
            raise ValueError("sample_rate must be between 0.0 and 1.0")
        if self.flush_at < 1 or self.flush_interval <= 0 or self.max_queue_size < 1:
            raise ValueError("flush_at, flush_interval and max_queue_size must be positive")

    @classmethod
    def from_env(cls) -> "TracingConfig":
        """``FORGEBENCH_TRACING_ENABLED``, ``FORGEBENCH_SAMPLE_RATE``,
        ``FORGEBENCH_ENVIRONMENT`` and ``FORGEBENCH_RELEASE``, else defaults.
        A value that does not parse is logged and ignored — a typo in the
        environment never stops the client from starting."""
        enabled = (os.environ.get("FORGEBENCH_TRACING_ENABLED") or "true").strip().lower()
        return cls(
            enabled=enabled not in ("0", "false", "no", "off"),
            sample_rate=_env_sample_rate(),
            environment=os.environ.get("FORGEBENCH_ENVIRONMENT") or None,
            release=os.environ.get("FORGEBENCH_RELEASE") or None,
        )


def _env_sample_rate() -> float:
    raw = (os.environ.get("FORGEBENCH_SAMPLE_RATE") or "").strip()
    if not raw:
        return 1.0
    try:
        rate = float(raw)
    except ValueError:
        rate = -1.0
    if 0.0 <= rate <= 1.0:
        return rate
    logger.warning("forgebench tracing: ignoring FORGEBENCH_SAMPLE_RATE=%r (want 0.0-1.0); using 1.0", raw)
    return 1.0
