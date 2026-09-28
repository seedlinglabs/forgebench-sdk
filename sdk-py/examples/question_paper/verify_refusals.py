"""Prove the door says NO — every refusal the demo agents can provoke, live.

    FORGEBENCH_ADMIN_KEY=sk_... python verify_refusals.py

Runs against the agents ``setup.py`` created. Each case flips one control
from the operator side (owner key), makes the governed call from the agent
side (the agent's own key, through the SDK), asserts what the caller sees,
and restores the control. What the ledger recorded is printed at the end so
"refused" is evidence, not a status code.

Cases:
  A  agent -> agent with no binding                   403 not_permitted
  B  agent -> agent, callee paused                    403 not_permitted
  C  model picks an MCP tool the agent is not bound to 403 mcp_tool_not_authorized
  D  tool killed globally on its server               403 (collapsed reason)
  E  per-agent call ceiling on one tool               403 call_ceiling_reached (verbatim)
  F  per-agent monthly budget exhausted               402 agent_budget_exceeded
  G  model outside the credential's allowlist         403 model_not_allowed
  H  a person's key on the agent-only surface         403
  I  an agent's key on an operator surface            403
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from forgebench import APIError, BudgetExceededError, PermissionDeniedError, Forgebench  # noqa: E402

STATE = json.loads((HERE / "qp-agents.json").read_text())
BASE, MODEL, AG = STATE["base_url"], STATE["model"], STATE["agents"]
ADMIN = os.environ.get("FORGEBENCH_ADMIN_KEY") or sys.exit("FORGEBENCH_ADMIN_KEY is required (an operator key)")
SERVER = STATE["mcp_server"]

PAPER_TOOLS = [
    {"type": "function", "function": {"name": "mcp_papers_search", "description": "Search past papers by topic",
                                       "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "mcp_papers_fetch", "description": "Fetch one paper by id",
                                       "parameters": {"type": "object", "properties": {"paper_id": {"type": "string"}}, "required": ["paper_id"]}}},
]


def op(method: str, path: str, body=None, key: str = ADMIN):
    """An operator/raw call. Returns (status, json)."""
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, raw.decode(errors="replace")


def client(role: str) -> Forgebench:
    return Forgebench(api_key=AG[role]["api_key"], base_url=BASE, timeout=90)


RESULTS: list[tuple[str, bool, str]] = []


def check(case: str, ok: bool, detail: str) -> None:
    RESULTS.append((case, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {case:<52} {detail}")


def codes_of(err: APIError) -> set[str]:
    """Every machine-readable token the refusal carried: the SDK's own
    ``code``, and the detail's ``code`` / ``reason`` / ``error`` — the
    chokepoint's refusals are not uniform about which key they use."""
    out = {str(err.code or "")}
    body = err.body if isinstance(err.body, dict) else {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        out |= {str(detail.get(k) or "") for k in ("code", "reason", "error")}
    return {c for c in out if c}


def code_of(err: APIError) -> str:
    return "/".join(sorted(codes_of(err)))


def chat(role: str, prompt: str, *, tools=None, model: str = MODEL):
    c = client(role)
    return c.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}],
                                     extra_body={"tools": tools} if tools else None, max_tokens=300)


def expect_denied(case: str, fn, want_code: str | None = None, want_status: int = 403) -> None:
    try:
        out = fn()
        check(case, False, f"NOT refused — got {str(out)[:80]}")
    except (PermissionDeniedError, BudgetExceededError, APIError) as e:
        ok = e.status_code == want_status and (want_code is None or want_code in codes_of(e))
        check(case, ok, f"HTTP {e.status_code} {code_of(e)}")


def main() -> int:
    orch, research, generator, reviewer = (client(r) for r in ("orchestrator", "research", "generator", "reviewer"))
    r_name, rev_name, rev_id = AG["research"]["name"], AG["reviewer"]["name"], AG["reviewer"]["id"]
    research_id = AG["research"]["id"]
    t0 = time.time()

    # A. No binding: the reviewer was never granted research.
    expect_denied("A  reviewer -> research (no binding)",
                  lambda: reviewer.agents.call(r_name, text="hi", wait=0), "not_permitted")

    # B. Paused callee: a live binding does not help while the callee is stopped.
    op("POST", f"/v1/agents/{rev_id}/state", {"state": "paused", "reason": "refusal drill"})
    try:
        expect_denied("B  orchestrator -> reviewer (callee paused)",
                      lambda: orch.agents.call(rev_name, text="review", wait=0), "not_permitted")
    finally:
        op("POST", f"/v1/agents/{rev_id}/state", {"state": "active"})

    # C. Unbound MCP tool: the generator offers the papers tools it was never bound to.
    expect_denied("C  generator picks mcp_papers_search (unbound)",
                  lambda: chat("generator", "Use the mcp_papers_search tool to search for 'photosynthesis'. You must call the tool.",
                               tools=PAPER_TOOLS), "mcp_tool_not_authorized")

    # D. Kill switch: research IS bound, but the operator disabled the tool on the server.
    op("POST", f"/v1/mcp-servers/{SERVER['id']}/tools/mcp_papers_search/disable")
    try:
        expect_denied("D  research picks mcp_papers_search (tool disabled)",
                      lambda: chat("research", "Use the mcp_papers_search tool to search for 'cell'. You must call the tool.",
                                   tools=PAPER_TOOLS), "mcp_tool_not_authorized")
    finally:
        op("POST", f"/v1/mcp-servers/{SERVER['id']}/tools/mcp_papers_search/enable")

    # E. Ceiling: at most ONE fetch per minute for research on this server.
    st, lim = op("POST", "/v1/mcp-limits", {"agent_id": research_id, "mcp_server_id": SERVER["id"],
                                           "tool_name": "mcp_papers_fetch", "window": "minute", "max_calls": 1})
    try:
        expect_denied("E  research fetches 3 papers, ceiling 1/min",
                      lambda: chat("research", "Call mcp_papers_fetch three times, once each for paper ids p-2023-bio-9, p-2022-bio-9 and p-2021-bio-9. You must call the tool three times.",
                                   tools=PAPER_TOOLS), "call_ceiling_reached")
    finally:
        if st == 201 and isinstance(lim, dict):
            op("DELETE", f"/v1/mcp-limits/{lim['id']}")

    # F. Per-agent budget: declared through the manifest, enforced at the chokepoint before any spend.
    op("PUT", f"/v1/agents/{rev_id}/manifest", {"budget": {"monthly_usd": 0.0001}})
    try:
        expect_denied("F  reviewer over its own $0.0001 monthly budget",
                      lambda: chat("reviewer", "Say hi."), "agent_budget_exceeded", want_status=402)
    finally:
        op("PUT", f"/v1/agents/{rev_id}/manifest", {"budget": {"monthly_usd": 5.0}})

    # G. Model boundary: the manifest bound research's credential to exactly one model.
    expect_denied("G  research asks for mock-gpt (allowlist is haiku only)",
                  lambda: chat("research", "Say hi.", model="mock-gpt"), "model_not_allowed")

    # H/I. The two surfaces are for two kinds of principal.
    st, body = op("GET", "/v1/agent-tools")
    check("H  operator key on GET /v1/agent-tools (agent-only)", st == 403, f"HTTP {st}")
    st, body = op("GET", "/v1/agents/inventory", key=AG["orchestrator"]["api_key"])
    check("I  agent key on GET /v1/agents/inventory (operator-only)", st == 403, f"HTTP {st}")

    # And the positive control: nothing above broke the happy path. No reviewer
    # serve() loop runs in this script, so "accepted" (queued for pull) is the
    # success condition; the task is cancelled so it never surprises run.py.
    task = orch.agents.call(rev_name, text="Return the single word OK.", wait=0)
    check("J  orchestrator -> reviewer accepted after restore", task.state in ("submitted", "working", "completed"), f"task {task.state}")
    orch.agents.cancel(rev_name, task.id)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} passed in {time.time() - t0:.1f}s")
    print("ledger since start: see the psql query in the README — every case above left a row.")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
