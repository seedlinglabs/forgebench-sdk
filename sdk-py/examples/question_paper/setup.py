"""Set up the question-paper workflow: four agents, one MCP server, manifests.

    python setup.py --admin-key sk_... [--base-url http://localhost:8000] [--model claude-haiku-4-5-20251001]

Runs as an operator (admin key). Everything it does is the operator's half of
the story: register the agents (each gets its own credential, printed once and
saved to ``qp-agents.json`` with mode 0600), register the MCP server the
research agent may use, and apply a manifest per agent — which is what binds
tools and grants agent → agent calls, approved on both sides because an admin
is applying them. ``run.py`` then plays the developer's half with the agents'
own keys and never touches the admin key.

Re-runnable: a fresh ``--suffix`` (default: a short random one) names a new
set of agents; pass the same suffix to reuse one.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "qp-agents.json"

AGENTS = {
    "orchestrator": {
        "description": "Plans a question paper, fans research out per topic, drives generation and review.",
        "tags": ["question-paper", "orchestrator"],
        "budget": 5.0,
    },
    "research": {
        "description": "Researches one syllabus topic with the papers tools and returns cited notes.",
        "tags": ["question-paper", "research"],
        "budget": 5.0,
    },
    "generator": {
        "description": "Writes exam questions and an answer key from research notes. Asks which board first.",
        "tags": ["question-paper", "generator"],
        "budget": 5.0,
    },
    "reviewer": {
        "description": "Reviews a generated paper for coverage, difficulty spread and ambiguity.",
        "tags": ["question-paper", "reviewer"],
        "budget": 5.0,
    },
}

PAPERS_TOOLS = [
    {"name": "mcp_papers_search", "description": "Search past exam papers and syllabus notes by topic. Args: {query: str, limit?: int}"},
    {"name": "mcp_papers_fetch", "description": "Fetch one paper's questions by id. Args: {paper_id: str}"},
]


def call(base: str, key: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} -> {e.code}: {detail[:600]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-key", default=os.environ.get("FORGEBENCH_ADMIN_KEY"), required=False)
    ap.add_argument("--base-url", default=os.environ.get("FORGEBENCH_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--model", default=os.environ.get("QP_MODEL", "claude-haiku-4-5-20251001"))
    ap.add_argument("--suffix", default=secrets.token_hex(2))
    ap.add_argument("--owner", default=os.environ.get("QP_OWNER_IDENTITY_ID"),
                    help="identity id of the accountable person; defaults to the key's own identity")
    args = ap.parse_args()
    if not args.admin_key:
        ap.error("--admin-key (or FORGEBENCH_ADMIN_KEY) is required")
    base = args.base_url.rstrip("/")
    key = args.admin_key

    me = call(base, key, "GET", "/v1/auth/whoami")
    owner = args.owner or me.get("identity_id")
    if not owner:
        raise SystemExit(
            "this key has no identity behind it (a machine key); pass --owner <identity id> "
            "for the person who will be accountable for these agents"
        )
    print(f"tenant {me['tenant_id']}  owner {owner}  roles {me.get('roles')}")

    # Models the gateway serves — fall back to mock-gpt if the requested one
    # is not in the catalog, so the flow runs on a keyless stack too.
    models = call(base, key, "GET", "/v1/models")
    served = {m["id"] for m in (models.get("data") or [])}
    model = args.model if (args.model in served or args.model.startswith("openrouter-")) else "mock-gpt"
    if model != args.model:
        print(f"note: {args.model!r} not in the catalog {sorted(served)[:6]}…; using mock-gpt")

    sfx = args.suffix
    state = {"base_url": base, "model": model, "suffix": sfx, "agents": {}}

    # 1. The MCP server the research agent may use (response-gate model: a
    #    catalog of qualified tool names; the agent's own client executes).
    server_name = f"papers-{sfx}"
    srv = call(base, key, "POST", "/v1/mcp-servers", {
        "name": server_name,
        "description": "Past exam papers and syllabus notes (demo).",
        "tools": PAPERS_TOOLS,
    })
    state["mcp_server"] = {"id": srv["id"], "name": server_name}
    print(f"mcp server {server_name} -> {srv['id']}")

    # 2. Register the agents. Each gets ITS OWN credential, bounded to the model.
    for role, spec in AGENTS.items():
        name = f"qp-{role}-{sfx}"
        reg = call(base, key, "POST", "/v1/agents/register", {
            "name": name,
            "owner_identity_id": owner,
            "description": spec["description"],
            "tags": spec["tags"],
            "model": model,
            "allowed_models": [model, "mock-gpt"] if model != "mock-gpt" else ["mock-gpt"],
            "monthly_budget_usd": spec["budget"],
        })
        state["agents"][role] = {
            "id": reg["agent"]["id"],
            "name": name,
            "api_key": reg["credential"]["api_key"],
        }
        print(f"registered {name} -> {reg['agent']['id']}  key {reg['credential']['prefix']}…")

    a = state["agents"]

    # 3. Manifests: what each agent needs. Applied by an admin, so tool
    #    bindings land live and agent bindings land approved on both sides.
    manifests = {
        "research": {"models": [model], "tools": [{"server": server_name, "tools": [t["name"] for t in PAPERS_TOOLS]}]},
        "generator": {"models": [model]},
        "reviewer": {"models": [model]},
        "orchestrator": {
            "models": [model],
            "calls": [a["research"]["name"], a["generator"]["name"], a["reviewer"]["name"]],
        },
    }
    for role, manifest in manifests.items():
        snap = call(base, key, "PUT", f"/v1/agents/{a[role]['id']}/manifest", manifest)
        print(f"manifest {a[role]['name']} v{snap['version']} {snap['manifest_hash'][:12]}  applied={json.dumps(snap['applied'])[:140]}")

    STATE.write_text(json.dumps(state, indent=2))
    os.chmod(STATE, 0o600)
    print(f"\nsaved {STATE} (agent keys inside — mode 0600)")
    print("next: python run.py --subject Biology --grade 9 --topics 3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
