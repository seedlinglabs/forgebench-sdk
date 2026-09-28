"""The integration most teams actually do: no Forgebench SDK at all.

    python openai_compat.py

The chokepoint is OpenAI-shaped, so the stock ``openai`` client (and anything
built on it — LangChain's ChatOpenAI, the OpenAI Agents SDK, LiteLLM) points
at the door with two settings: ``base_url`` and the agent's key. Tools pass
through, ``tool_calls`` come back, and the two things Forgebench adds ride
plain HTTP: ``call_id`` in the body, ``X-Parent-Call`` as a header.
"""

from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI, PermissionDeniedError

STATE = json.loads((Path(__file__).resolve().parent / "qp-agents.json").read_text())

client = OpenAI(
    base_url=f"{STATE['base_url']}/v1",          # the door
    api_key=STATE["agents"]["research"]["api_key"],  # the agent's own key
)

tools = [{"type": "function", "function": {
    "name": "mcp_papers_search", "description": "Search past papers by topic",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}}]

# 1. A governed model call, exactly as against OpenAI.
first = client.chat.completions.create(
    model=STATE["model"],
    messages=[{"role": "user", "content": "Search past papers about photosynthesis using the tool."}],
    tools=tools,
)
msg = first.choices[0].message
call_id = first.model_extra.get("call_id") if first.model_extra else None   # Forgebench's extra field
print("tool_calls:", [tc.function.name for tc in (msg.tool_calls or [])])
print("call_id   :", call_id)

# 2. The next turn, nested under the first via one header — the tree on the ledger.
follow = client.with_options(default_headers={"X-Parent-Call": call_id}).chat.completions.create(
    model=STATE["model"],
    messages=[
        {"role": "user", "content": "Search past papers about photosynthesis using the tool."},
        {"role": "assistant", "content": None, "tool_calls": [tc.model_dump() for tc in (msg.tool_calls or [])]},
        *[{"role": "tool", "tool_call_id": tc.id, "content": json.dumps({"hits": []})} for tc in (msg.tool_calls or [])],
    ],
)
print("child call_id:", follow.model_extra.get("call_id") if follow.model_extra else None, "(parent =", call_id, ")")

# 3. The door still says no through a stock client: the generator is not bound to this tool.
gen = OpenAI(base_url=f"{STATE['base_url']}/v1", api_key=STATE["agents"]["generator"]["api_key"])
try:
    gen.chat.completions.create(
        model=STATE["model"],
        messages=[{"role": "user", "content": "Use mcp_papers_search to search for 'cell'. You must call the tool."}],
        tools=tools,
    )
    print("refusal: NOT refused (model may have answered without calling the tool)")
except PermissionDeniedError as e:
    print("refusal:", e.status_code, (e.body or {}).get("detail", e.body) if isinstance(e.body, dict) else e.body)
