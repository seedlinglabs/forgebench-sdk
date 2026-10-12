import sys
import types

import pytest

from forgebench import SESSION_HEADER, TURN_HEADER, voice_headers
from forgebench import _trace


def test_session_and_turn():
    assert voice_headers("s1", turn=3) == {SESSION_HEADER: "s1", TURN_HEADER: "3"}
    assert voice_headers("s1") == {SESSION_HEADER: "s1"}


def test_explicit_traceparent_wins():
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    assert voice_headers("s", traceparent=tp)["traceparent"] == tp


def test_current_otel_span_becomes_traceparent(monkeypatch):
    ctx = types.SimpleNamespace(is_valid=True, trace_id=0x0AF7651916CD43DD8448EB211C80319C, span_id=0xB7AD6B7169203331, trace_flags=1)
    fake = types.SimpleNamespace(get_current_span=lambda: types.SimpleNamespace(get_span_context=lambda: ctx))
    mod = types.ModuleType("opentelemetry")
    mod.trace = fake
    monkeypatch.setitem(sys.modules, "opentelemetry", mod)
    assert voice_headers("s")["traceparent"] == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_without_opentelemetry_no_error_and_no_traceparent(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry", None)  # import raises ImportError
    assert "traceparent" not in voice_headers("s")
    assert _trace._current_traceparent() is None


@pytest.mark.parametrize("bad", ["", "x" * 129, "bad\nvalue", " lead", "café"])
def test_bad_session_rejected(bad):
    with pytest.raises(ValueError):
        voice_headers(bad)


def test_bad_turn_rejected():
    for t in (-1, 1.5, True):
        with pytest.raises(ValueError):
            voice_headers("s", turn=t)


def test_internal_consecutive_spaces_allowed():
    assert voice_headers("a  b")[SESSION_HEADER] == "a  b"
