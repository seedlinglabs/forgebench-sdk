"""Governed skills for an agent that runs on your own servers.

Skills live in Forgebench. An agent hosted there gets them composed for it; an
agent running in your process gets them through this hook::

    from openai import OpenAI
    from forgebench import SkillsHook

    hook = SkillsHook(api_key="sk_agent_...")          # the AGENT's key
    llm = hook.wrap(OpenAI(base_url=".../v1", api_key="sk_agent_..."))
    llm.chat.completions.create(model="...", messages=[...])  # skills included

What the hook does on each call:

1. Reads the agent's manifest (``GET /v1/skills/agent-manifest``). It is cached;
   after ``refresh_seconds`` it is re-checked with ``If-None-Match``, which costs
   one small request and returns 304 when nothing changed. :meth:`handle_webhook`
   (or :meth:`invalidate`) forces the re-check at once when Forgebench says a
   skill changed.
2. Composes the skills into the system messages the way a hosted run does:
   optional skills before your own system prompt, the workspace's mandatory
   skills after it under ``# MANDATORY POLICY``.
3. Sends ``X-Forgebench-Skills`` (the sha256 of each skill it composed), so the
   call is attributed to those exact versions.

If Forgebench cannot be reached, the last manifest keeps being used; with none
cached, the error is raised rather than calling the model without its policy.

The gateway does not have to trust this hook: with the agent's
``skill_hooks.enforcement`` set to ``inject`` or ``block`` it checks the prompt
itself, so a call that skips the hook still carries (or is refused without) the
mandatory skills.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import httpx

from ._client import _resolve_api_key, _resolve_base_url
from ._exceptions import ForgebenchError

log = logging.getLogger(__name__)

SKILLS_HEADER = "X-Forgebench-Skills"
SIGNATURE_HEADER = "X-Forgebench-Signature"
_MARKER = ".forgebench-skill"
_MANDATORY_HEADER = (
    "# MANDATORY POLICY\n\n"
    "The following policies are set by this organisation and OVERRIDE any "
    "conflicting instruction above. Follow them even when earlier "
    "instructions, or the user, ask you not to."
)


class SkillsUnavailableError(ForgebenchError):
    """No manifest could be read and none is cached."""


class WebhookVerificationError(ForgebenchError):
    """A webhook delivery's signature is missing, wrong, or too old."""


@dataclass(frozen=True)
class Skill:
    ref: str
    skill_id: str
    name: str
    version: int
    description: str
    mandatory: bool
    instructions: str
    sha256: str
    dir_name: str
    content: str

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Skill":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class SkillManifest:
    agent_id: str
    etag: str
    enforcement: str
    skills: Tuple[Skill, ...]

    @property
    def mandatory(self) -> Tuple[Skill, ...]:
        return tuple(s for s in self.skills if s.mandatory)

    @property
    def optional(self) -> Tuple[Skill, ...]:
        return tuple(s for s in self.skills if not s.mandatory)


def verify_webhook(
    body: bytes, signature: Optional[str], secret: str, *, tolerance: int = 300, now: Optional[float] = None
) -> Dict[str, Any]:
    """Check a delivery's ``X-Forgebench-Signature`` against the raw body and
    return the parsed event. Raises :class:`WebhookVerificationError`.

    The header is ``t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">``.
    Pass the body exactly as received: re-serialised JSON will not verify."""
    parts = dict(p.split("=", 1) for p in (signature or "").split(",") if "=" in p)
    try:
        ts = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        raise WebhookVerificationError("missing or malformed X-Forgebench-Signature") from None
    if abs((time.time() if now is None else now) - ts) > tolerance:
        raise WebhookVerificationError("signature timestamp is outside the tolerance; possible replay")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, given.strip().lower()):
        raise WebhookVerificationError("signature does not match")
    return json.loads(body)


class _HookCore:
    """State and logic shared by :class:`SkillsHook` and
    :class:`AsyncSkillsHook`; only the HTTP call differs."""

    def __init__(
        self,
        *,
        api_key: Optional[str],
        base_url: Optional[str],
        agent_id: Optional[str],
        refresh_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._api_key = _resolve_api_key(api_key)
        self._base_url = _resolve_base_url(base_url).rstrip("/")
        self._agent_id = agent_id
        self._refresh = refresh_seconds
        self._clock = clock
        self._manifest: Optional[SkillManifest] = None
        self._checked_at: Optional[float] = None

    def _cached(self, force: bool) -> Optional[SkillManifest]:
        fresh = self._checked_at is not None and self._clock() - self._checked_at < self._refresh
        return self._manifest if self._manifest is not None and fresh and not force else None

    def _request(self) -> Tuple[str, Dict[str, str], Optional[Dict[str, str]]]:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        if self._manifest is not None:
            headers["If-None-Match"] = self._manifest.etag
        params = {"agent_id": self._agent_id} if self._agent_id else None
        return f"{self._base_url}/v1/skills/agent-manifest", headers, params

    def _accept(self, r: httpx.Response) -> SkillManifest:
        if r.status_code == 304 and self._manifest is not None:
            self._checked_at = self._clock()
            return self._manifest
        if r.status_code != 200:
            return self._stale_or_raise(f"manifest request failed: HTTP {r.status_code} {r.text[:200]}")
        d = r.json()
        self._manifest = SkillManifest(
            agent_id=d["agent_id"],
            etag=d["etag"],
            enforcement=d["enforcement"],
            skills=tuple(Skill.from_dict(s) for s in d["skills"]),
        )
        self._checked_at = self._clock()
        return self._manifest

    def _stale_or_raise(self, why: str) -> SkillManifest:
        if self._manifest is None:
            raise SkillsUnavailableError(why)
        log.warning("forgebench skills: %s; using the cached manifest", why)
        return self._manifest

    def invalidate(self) -> None:
        """Re-check with the server on the next call (the cache is kept as the
        fallback if that check fails)."""
        self._checked_at = None


class SkillsHook(_HookCore):
    """Keeps an agent's governed skills current and composes them into calls.

    ``api_key`` should be the agent's own key (it reads only that agent's
    manifest). A member key works too with ``agent_id``. Call :meth:`apply`
    once per request: it adds the skills each time it is called. For asyncio
    code use :class:`AsyncSkillsHook`.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        refresh_seconds: float = 60.0,
        http_client: Optional[httpx.Client] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, agent_id=agent_id,
                         refresh_seconds=refresh_seconds, clock=clock)
        self._http = http_client or httpx.Client(timeout=10.0)

    def manifest(self, *, force: bool = False) -> SkillManifest:
        """The current manifest, re-checked with the server when stale."""
        cached = self._cached(force)
        if cached is not None:
            return cached
        url, headers, params = self._request()
        try:
            r = self._http.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            return self._stale_or_raise(f"could not reach Forgebench: {exc}")
        return self._accept(r)

    def handle_webhook(self, body: bytes, signature: Optional[str], secret: str) -> Dict[str, Any]:
        """Verify a ``skills.changed`` delivery and invalidate the cache. Return
        2xx from your endpoint after this; anything else is logged on our side."""
        event = verify_webhook(body, signature, secret)
        self.invalidate()
        return event

    def apply(self, messages: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        """``messages`` with the agent's skills added as system messages: the
        optional skills before your leading system messages, the mandatory
        policy right after them."""
        return _apply(self.manifest(), messages)

    def headers(self) -> Dict[str, str]:
        """``X-Forgebench-Skills`` for the skills :meth:`apply` composes."""
        return _headers(self.manifest())

    def wrap(self, client: Any) -> Any:
        """Make ``client.chat.completions.create`` add the skills to every call.
        Works with the OpenAI client and with :class:`forgebench.Forgebench`
        (both take ``messages`` and ``extra_headers``). Returns ``client``."""
        completions = client.chat.completions
        original = completions.create

        def create(*args: Any, messages: Sequence[Mapping[str, Any]], **kwargs: Any) -> Any:
            m = self.manifest()
            extra = {**_headers(m), **dict(kwargs.pop("extra_headers", None) or {})}
            return original(*args, messages=_apply(m, messages), extra_headers=extra, **kwargs)

        completions.create = create
        return client

    def write_files(self, directory: "str | Path") -> List[Path]:
        """Write each skill as ``<directory>/<dir_name>/SKILL.md`` for a runtime
        that loads skills from disk, and remove folders this hook wrote earlier
        that are no longer in the manifest. Folders it did not write are never
        touched. Returns the SKILL.md paths."""
        return _write_files(self.manifest(), Path(directory))


class AsyncSkillsHook(_HookCore):
    """:class:`SkillsHook` for asyncio code: the same behaviour, with
    ``await`` on everything that may reach the server. ``wrap`` works with
    ``openai.AsyncOpenAI`` and :class:`forgebench.AsyncForgebench`."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        refresh_seconds: float = 60.0,
        http_client: Optional[httpx.AsyncClient] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, agent_id=agent_id,
                         refresh_seconds=refresh_seconds, clock=clock)
        self._http = http_client or httpx.AsyncClient(timeout=10.0)

    async def manifest(self, *, force: bool = False) -> SkillManifest:
        cached = self._cached(force)
        if cached is not None:
            return cached
        url, headers, params = self._request()
        try:
            r = await self._http.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            return self._stale_or_raise(f"could not reach Forgebench: {exc}")
        return self._accept(r)

    def handle_webhook(self, body: bytes, signature: Optional[str], secret: str) -> Dict[str, Any]:
        """Synchronous on purpose: verifying needs no network, so it fits any
        web framework's handler."""
        event = verify_webhook(body, signature, secret)
        self.invalidate()
        return event

    async def apply(self, messages: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        return _apply(await self.manifest(), messages)

    async def headers(self) -> Dict[str, str]:
        return _headers(await self.manifest())

    def wrap(self, client: Any) -> Any:
        completions = client.chat.completions
        original = completions.create

        async def create(*args: Any, messages: Sequence[Mapping[str, Any]], **kwargs: Any) -> Any:
            m = await self.manifest()
            extra = {**_headers(m), **dict(kwargs.pop("extra_headers", None) or {})}
            return await original(*args, messages=_apply(m, messages), extra_headers=extra, **kwargs)

        completions.create = create
        return client

    async def write_files(self, directory: "str | Path") -> List[Path]:
        return _write_files(await self.manifest(), Path(directory))

    async def aclose(self) -> None:
        await self._http.aclose()


def _apply(m: SkillManifest, messages: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    msgs = [dict(x) for x in messages]
    lead = next((i for i, x in enumerate(msgs) if x.get("role") not in ("system", "developer")), len(msgs))
    before = [{"role": "system", "content": _skills_block(m.optional)}] if m.optional else []
    after = [{"role": "system", "content": _mandatory_block(m.mandatory)}] if m.mandatory else []
    return [*before, *msgs[:lead], *after, *msgs[lead:]]


def _headers(m: SkillManifest) -> Dict[str, str]:
    return {SKILLS_HEADER: ",".join(s.sha256 for s in m.skills)} if m.skills else {}


def _write_files(m: SkillManifest, root: Path) -> List[Path]:
    root.mkdir(parents=True, exist_ok=True)
    keep = {s.dir_name for s in m.skills}
    for child in root.iterdir():
        if child.is_dir() and (child / _MARKER).exists() and child.name not in keep:
            shutil.rmtree(child)
    written = []
    for s in m.skills:
        if not s.dir_name or s.dir_name in (".", "..") or "/" in s.dir_name or "\\" in s.dir_name:
            raise SkillsUnavailableError(f"refusing to write a skill folder named {s.dir_name!r}")
        folder = root / s.dir_name
        folder.mkdir(exist_ok=True)
        path = folder / "SKILL.md"
        path.write_text(s.content, encoding="utf-8")
        (folder / _MARKER).write_text(json.dumps({"ref": s.ref, "sha256": s.sha256}), encoding="utf-8")
        written.append(path)
    return written


def _skills_block(skills: Sequence[Skill]) -> str:
    return "\n\n".join(f"# Skill: {s.ref}\n\n{s.instructions}" for s in skills)


def _mandatory_block(skills: Sequence[Skill]) -> str:
    return "\n\n".join([_MANDATORY_HEADER, *(f"## Policy: {s.ref}\n\n{s.instructions}" for s in skills)])
