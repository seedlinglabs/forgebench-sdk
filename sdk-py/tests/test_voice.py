"""Offline tests for the voice surface of the Python SDK.

Mocked with respx at the HTTP boundary and asserted through the public SDK
methods only. Expected wire values are literals taken from the release plan's
B and F contracts (multipart field names, header names, routes), not derived
from the SDK's own constants.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from forgebench import (
    AsyncForgebench,
    BudgetExceededError,
    Forgebench,
    PermissionDeniedError,
    ValidationError,
)

BASE = "http://testserver"
KEY = "sk_test_abc123"
TRACE = "a" * 32
AGENT = "11111111-1111-1111-1111-111111111111"


def _client() -> Forgebench:
    return Forgebench(api_key=KEY, base_url=BASE, max_retries=0)


def _multipart_fields(request: httpx.Request) -> dict:
    """Crude multipart parse: field name -> (part headers, raw body)."""
    ctype = request.headers["content-type"]
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=")[1].encode()
    out = {}
    for part in request.content.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        out[name] = (head, body.rstrip(b"\r\n"))
    return out


@respx.mock
def test_transcription_sends_multipart_and_session_header():
    seen = {}

    def responder(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(200, json={"text": "hello world"})

    respx.post(f"{BASE}/v1/audio/transcriptions").mock(side_effect=responder)

    with _client() as client:
        out = client.audio.transcriptions.create(
            ("clip.wav", b"RIFFfake", "audio/wav"),
            "gpt-4o-transcribe",
            language="en",
            prompt="Acme",
            response_format="json",
            metadata={"channel": "phone"},
            session="conv-1",
        )

    req = seen["req"]
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["x-forgebench-session"] == "conv-1"
    fields = _multipart_fields(req)
    assert fields["model"][1] == b"gpt-4o-transcribe"
    assert fields["language"][1] == b"en"
    assert fields["prompt"][1] == b"Acme"
    assert fields["response_format"][1] == b"json"
    assert json.loads(fields["metadata"][1]) == {"channel": "phone"}
    assert b'filename="clip.wav"' in fields["file"][0]
    assert fields["file"][1] == b"RIFFfake"
    assert out.text == "hello world"


@respx.mock
def test_transcription_omits_unset_optional_fields_and_accepts_bare_bytes():
    seen = {}

    def responder(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(200, json={"text": "x"})

    respx.post(f"{BASE}/v1/audio/transcriptions").mock(side_effect=responder)
    with _client() as client:
        client.audio.transcriptions.create(b"\x00\x01", "whisper-1")

    fields = _multipart_fields(seen["req"])
    assert set(fields) == {"file", "model"}
    assert "x-forgebench-session" not in seen["req"].headers


@respx.mock
def test_transcription_text_response_format_returns_text():
    respx.post(f"{BASE}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, text="plain words", headers={"content-type": "text/plain"})
    )
    with _client() as client:
        out = client.audio.transcriptions.create(b"a", "whisper-1", response_format="text")
    assert out.text == "plain words"


@respx.mock
def test_transcription_model_not_allowed_surfaces_nested_code():
    respx.post(f"{BASE}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(
            403, json={"detail": {"error": "model_not_allowed", "message": "model not allowed"}}
        )
    )
    with _client() as client:
        with pytest.raises(PermissionDeniedError) as ei:
            client.audio.transcriptions.create(b"a", "whisper-1")
    assert ei.value.code == "model_not_allowed"


@respx.mock
def test_top_level_error_envelope_is_parsed():
    respx.post(f"{BASE}/v1/audio/speech").mock(
        return_value=httpx.Response(
            403, json={"error": {"code": "model_not_allowed", "message": "nope"}}
        )
    )
    with _client() as client:
        with pytest.raises(PermissionDeniedError) as ei:
            client.audio.speech.create("tts-1", "hi", "alloy")
    assert ei.value.code == "model_not_allowed"
    assert "nope" in str(ei.value)


@respx.mock
def test_transcription_budget_exhausted_is_budget_error():
    respx.post(f"{BASE}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(402, json={"detail": "budget exceeded", "code": "budget_exceeded"})
    )
    with _client() as client:
        with pytest.raises(BudgetExceededError):
            client.audio.transcriptions.create(b"a", "whisper-1")


@respx.mock
def test_speech_returns_bytes_and_trace_id():
    seen = {}

    def responder(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(
            200,
            content=b"ID3\x04audio",
            headers={"content-type": "audio/mpeg", "X-Forgebench-Trace-Id": TRACE},
        )

    respx.post(f"{BASE}/v1/audio/speech").mock(side_effect=responder)
    with _client() as client:
        out = client.audio.speech.create(
            "gpt-4o-mini-tts",
            "I am an AI assistant.",
            "alloy",
            response_format="mp3",
            speed=1.25,
            metadata={"k": "v"},
            session="conv-1",
        )

    req = seen["req"]
    assert req.headers["x-forgebench-session"] == "conv-1"
    assert json.loads(req.content) == {
        "model": "gpt-4o-mini-tts",
        "input": "I am an AI assistant.",
        "voice": "alloy",
        "response_format": "mp3",
        "speed": 1.25,
        "metadata": {"k": "v"},
    }
    assert out.audio == b"ID3\x04audio"
    assert out.content_type == "audio/mpeg"
    assert out.trace_id == TRACE


@respx.mock
def test_speech_minimal_body_and_disclosure_refusal_is_validation_error():
    seen = {}

    def responder(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            422, json={"detail": {"code": "disclosure_missing", "message": "say you are an AI"}}
        )

    respx.post(f"{BASE}/v1/audio/speech").mock(side_effect=responder)
    with _client() as client:
        with pytest.raises(ValidationError) as ei:
            client.audio.speech.create("tts-1", "Hi", "alloy")
    assert seen["body"] == {"model": "tts-1", "input": "Hi", "voice": "alloy"}
    assert ei.value.code == "disclosure_missing"


@respx.mock
def test_agent_run_stream_yields_sse_events():
    seen = {}
    def chunk(text: str) -> str:
        return "data: " + json.dumps(
            {"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": text}}]}
        ) + "\n\n"

    completed = {"type": "agent.run.completed", "answer": "Hello", "trace_id": TRACE, "cost_usd": "0.000010", "citations": []}
    sse = chunk("Hel") + chunk("lo") + "data: " + json.dumps(completed) + "\n\n" + "data: [DONE]\n\n"

    def responder(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    respx.post(f"{BASE}/v1/agents/{AGENT}/run").mock(side_effect=responder)
    with _client() as client:
        events = list(client.agents.run(AGENT, message="hi", stream=True, session="conv-9"))

    assert json.loads(seen["req"].content) == {"message": "hi", "stream": True}
    assert seen["req"].headers["x-forgebench-session"] == "conv-9"
    assert [e.get("object") or e.get("type") for e in events] == [
        "chat.completion.chunk", "chat.completion.chunk", "agent.run.completed",
    ]
    assert events[0]["choices"][0]["delta"]["content"] == "Hel"
    assert events[-1]["answer"] == "Hello" and events[-1]["trace_id"] == TRACE


@respx.mock
def test_agent_run_message_without_stream_returns_answer_and_legacy_path_unchanged():
    respx.post(f"{BASE}/v1/agents/{AGENT}/run").mock(
        return_value=httpx.Response(200, json={"answer": "yo", "trace_id": TRACE})
    )
    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(200, json={"id": "r1", "status": "succeeded", "model": "mock-gpt"})
    )
    with _client() as client:
        ans = client.agents.run(AGENT, message="hi")
        legacy = client.agents.run(AGENT, input={"x": 1})
    assert ans["answer"] == "yo"
    assert legacy.id == "r1"


@respx.mock
def test_voice_admin_helpers_hit_f_routes():
    respx.get(f"{BASE}/v1/voice/policy").mock(
        return_value=httpx.Response(200, json={"disclosure_mode": "log"})
    )
    upd = respx.put(f"{BASE}/v1/voice/policy").mock(
        return_value=httpx.Response(200, json={"disclosure_mode": "enforce"})
    )
    respx.get(f"{BASE}/v1/voice/assets").mock(return_value=httpx.Response(200, json=[{"id": "a1"}]))
    crt = respx.post(f"{BASE}/v1/voice/assets").mock(
        return_value=httpx.Response(201, json={"id": "a2"})
    )
    patch = respx.patch(f"{BASE}/v1/voice/assets/a2").mock(
        return_value=httpx.Response(200, json={"id": "a2", "display_name": "B2"})
    )
    respx.post(f"{BASE}/v1/voice/assets/a2/revoke").mock(
        return_value=httpx.Response(200, json={"id": "a2", "status": "revoked"})
    )
    respx.get(f"{BASE}/v1/voice/consents").mock(return_value=httpx.Response(200, json=[]))
    ccrt = respx.post(f"{BASE}/v1/voice/consents").mock(
        return_value=httpx.Response(201, json={"id": "c1"})
    )
    respx.post(f"{BASE}/v1/voice/consents/c1/revoke").mock(
        return_value=httpx.Response(200, json={"id": "c1", "revoked_at": "2026-10-09T00:00:00Z"})
    )
    with _client() as client:
        assert client.voice.policy.get()["disclosure_mode"] == "log"
        assert client.voice.policy.update(disclosure_mode="enforce")["disclosure_mode"] == "enforce"
        assert client.voice.assets.list() == [{"id": "a1"}]
        assert (
            client.voice.assets.create(
                provider="elevenlabs",
                provider_voice_id="v1",
                display_name="Brand",
                kind="cloned",
                consent_record_id="c1",
            )["id"]
            == "a2"
        )
        assert client.voice.assets.update("a2", display_name="B2")["display_name"] == "B2"
        assert client.voice.assets.revoke("a2")["status"] == "revoked"
        assert client.voice.consents.list() == []
        assert (
            client.voice.consents.create(
                subject_name="Jane", purpose="voice_clone", scope="Support agent voice"
            )["id"]
            == "c1"
        )
        assert client.voice.consents.revoke("c1")["revoked_at"] is not None

    assert json.loads(upd.calls[0].request.content) == {"disclosure_mode": "enforce"}
    assert json.loads(crt.calls[0].request.content) == {
        "provider": "elevenlabs",
        "provider_voice_id": "v1",
        "display_name": "Brand",
        "kind": "cloned",
        "consent_record_id": "c1",
    }
    assert json.loads(patch.calls[0].request.content) == {"display_name": "B2"}
    assert json.loads(ccrt.calls[0].request.content) == {
        "subject_name": "Jane",
        "purpose": "voice_clone",
        "scope": "Support agent voice",
    }


# --- async parity ----------------------------------------------------------
@respx.mock
async def test_async_transcription_speech_and_stream():
    respx.post(f"{BASE}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={"text": "async hi"})
    )
    respx.post(f"{BASE}/v1/audio/speech").mock(
        return_value=httpx.Response(
            200, content=b"pcm", headers={"X-Forgebench-Trace-Id": TRACE, "content-type": "audio/pcm"}
        )
    )
    respx.post(f"{BASE}/v1/agents/{AGENT}/run").mock(
        return_value=httpx.Response(
            200,
            text='data: {"type": "delta", "text": "a"}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )
    respx.get(f"{BASE}/v1/voice/policy").mock(return_value=httpx.Response(200, json={"x": 1}))

    async with AsyncForgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        t = await client.audio.transcriptions.create(b"a", "whisper-1", session="s")
        s = await client.audio.speech.create("tts-1", "hi", "alloy")
        events = [e async for e in await client.agents.run(AGENT, message="hi", stream=True)]
        pol = await client.voice.policy.get()

    assert t.text == "async hi"
    assert (s.audio, s.trace_id) == (b"pcm", TRACE)
    assert events == [{"type": "delta", "text": "a"}]
    assert pol == {"x": 1}


# --- stream failures, policy round-trip, clearing asset fields -------------
def _sse(text: str) -> None:
    respx.post(f"{BASE}/v1/agents/{AGENT}/run").mock(
        return_value=httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
    )


@respx.mock
def test_agent_stream_error_frame_raises():
    from forgebench import StreamError

    _sse('data: {"type": "delta", "text": "a"}\n\nevent: error\ndata: {"error": {"code": "upstream_failed", "message": "boom"}}\n\n')
    with _client() as client, pytest.raises(StreamError) as ei:
        list(client.agents.run(AGENT, message="hi", stream=True))
    assert ei.value.code == "upstream_failed" and ei.value.message == "boom"


@respx.mock
def test_agent_stream_truncated_raises_but_completed_without_done_ok():
    from forgebench import StreamError

    _sse('data: {"type": "delta", "text": "a"}\n\n')
    with _client() as client, pytest.raises(StreamError) as ei:
        list(client.agents.run(AGENT, message="hi", stream=True))
    assert ei.value.code == "stream_truncated"
    _sse('data: {"type": "agent.run.completed", "answer": "x"}\n\n')
    with _client() as client:
        assert len(list(client.agents.run(AGENT, message="hi", stream=True))) == 1


@respx.mock
async def test_async_agent_stream_error_frame_raises():
    from forgebench import StreamError

    _sse('event: error\ndata: {"error": {"message": "boom"}}\n\n')
    async with AsyncForgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        with pytest.raises(StreamError):
            [e async for e in await client.agents.run(AGENT, message="hi", stream=True)]


@respx.mock
def test_policy_update_drops_updated_at_and_asset_update_can_clear():
    put = respx.put(f"{BASE}/v1/voice/policy").mock(return_value=httpx.Response(200, json={}))
    patch = respx.patch(f"{BASE}/v1/voice/assets/a1").mock(return_value=httpx.Response(200, json={}))
    with _client() as client:
        client.voice.policy.update(disclosure_mode="log", updated_at="2026-01-01", voice_trace_content=False)
        client.voice.assets.update("a1", expires_at=None)
    assert json.loads(put.calls[0].request.content) == {"disclosure_mode": "log", "voice_trace_content": False}
    assert json.loads(patch.calls[0].request.content) == {"expires_at": None}


def test_trace_header_constants():
    from forgebench import AUDIO_TRACE_ID_HEADER, TRACE_ID_HEADER

    assert (TRACE_ID_HEADER, AUDIO_TRACE_ID_HEADER) == ("X-Trace-Id", "X-Forgebench-Trace-Id")
