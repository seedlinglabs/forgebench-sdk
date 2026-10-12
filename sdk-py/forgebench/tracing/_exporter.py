"""Background sender: finished observations are queued here and posted to
``/v1/observations/batch`` from one daemon thread, so tracing never makes the
agent wait. Never raises into callers; a failed send is logged and dropped."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger("forgebench.tracing")

#: The server takes at most this many observations per batch.
MAX_BATCH = 100

Send = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], None]


class BatchExporter:
    def __init__(
        self,
        send: Send,
        *,
        flush_at: int,
        flush_interval: float,
        max_queue_size: int,
    ) -> None:
        self._send = send
        self._flush_at = min(flush_at, MAX_BATCH)
        self._flush_interval = flush_interval
        self._max_queue_size = max_queue_size
        self._queue: Deque[Dict[str, Any]] = deque()
        self._traces: Dict[str, Dict[str, Any]] = {}
        self._cond = threading.Condition()
        self._sending = False
        self._flushing = False
        self._closed = False
        self._dropped = 0
        self._thread: Optional[threading.Thread] = None

    @property
    def dropped(self) -> int:
        return self._dropped

    def add(self, observation: Dict[str, Any], trace: Optional[Dict[str, Any]] = None) -> None:
        with self._cond:
            if self._closed:
                return
            if len(self._queue) >= self._max_queue_size:
                self._queue.popleft()
                self._dropped += 1
                if self._dropped == 1:
                    logger.warning(
                        "forgebench tracing: queue full (%d), dropping the oldest observations",
                        self._max_queue_size,
                    )
            self._queue.append(observation)
            if trace is not None:
                self._traces[trace["id"]] = {**self._traces.get(trace["id"], {}), **trace}
            self._start_thread()
            if len(self._queue) >= self._flush_at:
                self._cond.notify_all()

    def flush(self, timeout: float = 5.0) -> bool:
        """Send everything queued now. True when it all went out in time."""
        with self._cond:
            if self._thread is None:
                return True
            self._flushing = True
            self._cond.notify_all()
            done = self._cond.wait_for(self._idle, timeout=timeout)
            self._flushing = False
            return done

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush, then stop the sender thread. Later observations are ignored."""
        self.flush(timeout)
        with self._cond:
            self._closed = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- sender thread ------------------------------------------------------
    def _idle(self) -> bool:
        return not self._queue and not self._traces and not self._sending

    def _start_thread(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="forgebench-tracing", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not (self._queue or self._traces):
                    if self._closed:
                        return
                    self._cond.wait()
                if len(self._queue) < self._flush_at and not (self._flushing or self._closed):
                    # Not a full batch yet: give stragglers until the interval.
                    self._cond.wait(timeout=self._flush_interval)
                observations, traces = self._take()
                self._sending = True
            try:
                self._send(observations, traces)
            except Exception as exc:  # noqa: BLE001 - tracing never fails the agent
                logger.warning("forgebench tracing: sending %d observations failed: %s", len(observations), exc)
            finally:
                with self._cond:
                    self._sending = False
                    self._cond.notify_all()

    def _take(self):
        """The next batch. A trace record always travels with steps of its own
        trace, since the server attaches trace fields to a step in the same batch."""
        observations = [self._queue.popleft() for _ in range(min(MAX_BATCH, len(self._queue)))]
        in_batch = {o.get("trace_id") for o in observations}
        trace_ids = [t for t in self._traces if t in in_batch or not self._queue][:MAX_BATCH]
        traces = [self._traces.pop(trace_id) for trace_id in trace_ids]
        return observations, traces
