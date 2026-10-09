"""``client.langfuse`` — your workspace's Langfuse data through the governed
passthrough (``/v1/langfuse/*``).

The control plane holds the Langfuse keys and exposes an allowlisted subset of
Langfuse's public API (pinned OpenAPI; see the control plane's
``app.routers.langfuse_passthrough``). Reads need admin, ``traces:read`` or the
``observability:view`` permission; writes (scores, comments, prompts, datasets,
dataset items, annotation-queue items) also need builder+, are redacted and
audited server side. Anything else is a 404.

Wrappers are thin: query params and bodies use Langfuse's own (camelCase) names
and responses are Langfuse's JSON, unchanged. The same classes serve the sync
and async clients — on :class:`forgebench.AsyncForgebench` every method returns
an awaitable.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

_PREFIX = "/v1/langfuse"


def _seg(value: Any) -> str:
    """One path segment, percent-encoded (prompt/dataset names may hold ``/``)."""
    return quote(str(value), safe="")


class _Group:
    def __init__(self, transport: Any) -> None:
        self._t = transport

    def _get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self._t.request("GET", _PREFIX + path, params=dict(params or {}))

    def _send(self, method: str, path: str, body: Mapping[str, Any]) -> Any:
        return self._t.request(method, _PREFIX + path, json_body=dict(body))


class LangfuseTraces(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/traces", params)

    def get(self, trace_id: str) -> Any:
        return self._get(f"/traces/{_seg(trace_id)}")


class LangfuseSessions(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/sessions", params)

    def get(self, session_id: str) -> Any:
        return self._get(f"/sessions/{_seg(session_id)}")


class LangfuseObservations(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/observations", params)

    def list_v2(self, **params: Any) -> Any:
        return self._get("/v2/observations", params)

    def get(self, observation_id: str) -> Any:
        return self._get(f"/observations/{_seg(observation_id)}")


class LangfuseMetrics(_Group):
    def query(self, query: Mapping[str, Any]) -> Any:
        """Langfuse metrics query (v1 ``/metrics``); ``query`` is the query object."""
        return self._get("/metrics", {"query": json.dumps(query)})

    def query_v2(self, query: Mapping[str, Any]) -> Any:
        return self._get("/v2/metrics", {"query": json.dumps(query)})


class LangfuseScores(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/v2/scores", params)

    def list_v3(self, **params: Any) -> Any:
        return self._get("/v3/scores", params)

    def get(self, score_id: str) -> Any:
        return self._get(f"/v2/scores/{_seg(score_id)}")

    def create(self, **body: Any) -> Any:
        """e.g. ``create(traceId=..., name="helpful", value=1, comment=...)``."""
        return self._send("POST", "/scores", body)


class LangfuseScoreConfigs(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/score-configs", params)

    def get(self, config_id: str) -> Any:
        return self._get(f"/score-configs/{_seg(config_id)}")


class LangfusePrompts(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/v2/prompts", params)

    def get(self, name: str, **params: Any) -> Any:
        """``params``: ``version`` or ``label``."""
        return self._get(f"/v2/prompts/{_seg(name)}", params)

    def create(self, **body: Any) -> Any:
        return self._send("POST", "/v2/prompts", body)


class LangfuseDatasets(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/v2/datasets", params)

    def get(self, name: str) -> Any:
        return self._get(f"/v2/datasets/{_seg(name)}")

    def create(self, **body: Any) -> Any:
        return self._send("POST", "/v2/datasets", body)

    def list_runs(self, name: str, **params: Any) -> Any:
        return self._get(f"/datasets/{_seg(name)}/runs", params)

    def get_run(self, name: str, run_name: str) -> Any:
        return self._get(f"/datasets/{_seg(name)}/runs/{_seg(run_name)}")


class LangfuseDatasetItems(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/dataset-items", params)

    def get(self, item_id: str) -> Any:
        return self._get(f"/dataset-items/{_seg(item_id)}")

    def create(self, **body: Any) -> Any:
        return self._send("POST", "/dataset-items", body)


class LangfuseDatasetRunItems(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/dataset-run-items", params)


class LangfuseComments(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/comments", params)

    def get(self, comment_id: str) -> Any:
        return self._get(f"/comments/{_seg(comment_id)}")

    def create(self, **body: Any) -> Any:
        return self._send("POST", "/comments", body)


class LangfuseAnnotationQueues(_Group):
    def list(self, **params: Any) -> Any:
        return self._get("/annotation-queues", params)

    def get(self, queue_id: str) -> Any:
        return self._get(f"/annotation-queues/{_seg(queue_id)}")

    def list_items(self, queue_id: str, **params: Any) -> Any:
        return self._get(f"/annotation-queues/{_seg(queue_id)}/items", params)

    def get_item(self, queue_id: str, item_id: str) -> Any:
        return self._get(f"/annotation-queues/{_seg(queue_id)}/items/{_seg(item_id)}")

    def create_item(self, queue_id: str, **body: Any) -> Any:
        return self._send("POST", f"/annotation-queues/{_seg(queue_id)}/items", body)

    def update_item(self, queue_id: str, item_id: str, **body: Any) -> Any:
        return self._send("PATCH", f"/annotation-queues/{_seg(queue_id)}/items/{_seg(item_id)}", body)


class Langfuse(_Group):
    """Entry point: ``client.langfuse.traces.list(userId="u1")`` etc."""

    def __init__(self, transport: Any) -> None:
        super().__init__(transport)
        self.traces = LangfuseTraces(transport)
        self.sessions = LangfuseSessions(transport)
        self.observations = LangfuseObservations(transport)
        self.metrics = LangfuseMetrics(transport)
        self.scores = LangfuseScores(transport)
        self.score_configs = LangfuseScoreConfigs(transport)
        self.prompts = LangfusePrompts(transport)
        self.datasets = LangfuseDatasets(transport)
        self.dataset_items = LangfuseDatasetItems(transport)
        self.dataset_run_items = LangfuseDatasetRunItems(transport)
        self.comments = LangfuseComments(transport)
        self.annotation_queues = LangfuseAnnotationQueues(transport)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Escape hatch for an allowlisted op without a wrapper; ``path`` is the
        Langfuse path minus ``/api/public`` (e.g. ``"/v2/prompts"``)."""
        return self._t.request(method, _PREFIX + path, params=params, json_body=json_body)
