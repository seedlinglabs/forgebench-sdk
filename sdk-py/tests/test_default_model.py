"""A chosen production default must produce one request, never a mock probe."""

import json

import httpx
import pytest
import respx

from forgebench import AsyncForgebench, Forgebench

BASE = "http://default-model.test"
VERTEX = "vertex-gemini-2.5-pro"


def response(request):
    body = json.loads(request.content)
    return httpx.Response(200, json={
        "id": "fixture", "object": "chat.completion", "created": 0,
        "model": body["model"], "choices": [], "usage": {},
    })


@pytest.mark.parametrize("override,expected", [(None, VERTEX), ("mock-gpt", "mock-gpt")])
@respx.mock
def test_sync_configured_default_sends_once_and_explicit_model_wins(override, expected):
    route = respx.post(BASE + "/v1/chat/completions").mock(side_effect=response)
    with Forgebench(api_key="fixture", base_url=BASE, default_model=VERTEX, max_retries=0) as client:
        options = {} if override is None else {"model": override}
        result = client.chat.completions.create(messages=[{"role": "user", "content": "hello"}], **options)
    assert result.model == expected
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content)["model"] == expected


@pytest.mark.parametrize("override,expected", [(None, VERTEX), ("mock-gpt", "mock-gpt")])
@respx.mock
async def test_async_configured_default_sends_once_and_explicit_model_wins(override, expected):
    route = respx.post(BASE + "/v1/chat/completions").mock(side_effect=response)
    async with AsyncForgebench(api_key="fixture", base_url=BASE, default_model=VERTEX, max_retries=0) as client:
        options = {} if override is None else {"model": override}
        result = await client.chat.completions.create(messages=[{"role": "user", "content": "hello"}], **options)
    assert result.model == expected
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content)["model"] == expected


@respx.mock
def test_unconfigured_client_preserves_legacy_mock_default():
    route = respx.post(BASE + "/v1/chat/completions").mock(side_effect=response)
    with Forgebench(api_key="fixture", base_url=BASE, max_retries=0) as client:
        assert client.chat.completions.create(messages=[{"role": "user", "content": "hello"}]).model == "mock-gpt"
    assert route.call_count == 1
