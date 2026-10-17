"""Polling lifetime is separate from bounded individual HTTP requests."""
from types import SimpleNamespace

import pytest

from forgebench import _async_resources, _resources
from forgebench._exceptions import ForgebenchError


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    async def asleep(self, seconds):
        self.sleep(seconds)


class Transport:
    def __init__(self, clock, finish_after=3):
        self.clock = clock
        self.finish_after = finish_after
        self.polls = 0
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        if method == "POST":
            status = "queued"
        else:
            self.polls += 1
            self.clock.now += 65
            status = "succeeded" if self.polls >= self.finish_after else "running"
        return {"id": "retained-run", "status": status, "model": "mock-gpt", "input": {}}


class AsyncTransport(Transport):
    async def request(self, method, path, **kwargs):
        return super().request(method, path, **kwargs)


@pytest.mark.parametrize("through_agent", [False, True])
def test_sync_default_wait_survives_past_two_minutes(monkeypatch, through_agent):
    clock = Clock()
    monkeypatch.setattr(_resources, "time", clock)
    transport = Transport(clock)
    result = (_resources.Agents(transport).run("agent", wait=True, poll_interval=0)
              if through_agent else _resources.Runs(transport).wait("retained-run", poll_interval=0))
    assert result.status == "succeeded"
    assert clock.now == 195
    assert transport.polls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("through_agent", [False, True])
async def test_async_default_wait_survives_past_two_minutes(monkeypatch, through_agent):
    clock = Clock()
    monkeypatch.setattr(_async_resources, "time", clock)
    monkeypatch.setattr(_async_resources, "asyncio", SimpleNamespace(sleep=clock.asleep))
    transport = AsyncTransport(clock)
    result = (await _async_resources.AsyncAgents(transport).run("agent", wait=True, poll_interval=0)
              if through_agent else await _async_resources.AsyncRuns(transport).wait("retained-run", poll_interval=0))
    assert result.status == "succeeded"
    assert clock.now == 195
    assert transport.polls == 3


def test_sync_explicit_wait_deadline_does_not_cancel_run(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(_resources, "time", clock)
    transport = Transport(clock, finish_after=10)
    runs = _resources.Runs(transport)
    with pytest.raises(ForgebenchError, match="retained-run.*Polling stopped; the run was not cancelled"):
        runs.wait("retained-run", timeout=5, poll_interval=0)
    assert runs.get("retained-run").status == "running"
    assert transport.calls == [("GET", "/v1/runs/retained-run")] * 2


@pytest.mark.asyncio
async def test_async_explicit_wait_deadline_does_not_cancel_run(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(_async_resources, "time", clock)
    monkeypatch.setattr(_async_resources, "asyncio", SimpleNamespace(sleep=clock.asleep))
    transport = AsyncTransport(clock, finish_after=10)
    runs = _async_resources.AsyncRuns(transport)
    with pytest.raises(ForgebenchError, match="retained-run.*Polling stopped; the run was not cancelled"):
        await runs.wait("retained-run", timeout=5, poll_interval=0)
    assert (await runs.get("retained-run")).status == "running"
    assert transport.calls == [("GET", "/v1/runs/retained-run")] * 2
