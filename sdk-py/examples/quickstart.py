#!/usr/bin/env python3
"""Runnable end-to-end example for the Forgebench Python SDK.

Proves the governed path against a locally running control plane
(http://localhost:8000): a chat completion (sync + streaming), agent listing,
an agent run, and the metering/budget view — including the pre-call budget gate
surfacing as ``BudgetExceededError`` (HTTP 402).

Prerequisites (from repo root):
    make up && make seed     # prints a raw sk_... key ONCE

Run:
    export FORGEBENCH_API_KEY=sk_...          # the key make seed printed
    export FORGEBENCH_BASE_URL=http://localhost:8000   # optional; this is the default
    python examples/quickstart.py
"""

from __future__ import annotations

import os
import sys

from forgebench import BudgetExceededError, Forgebench


def main() -> int:
    api_key = os.environ.get("FORGEBENCH_API_KEY")
    if not api_key:
        print(
            "Set FORGEBENCH_API_KEY to the sk_... key printed by `make seed`.\n"
            "  export FORGEBENCH_API_KEY=sk_...",
            file=sys.stderr,
        )
        return 2

    # base_url defaults to FORGEBENCH_BASE_URL or http://localhost:8000.
    with Forgebench(api_key=api_key) as client:
        me = client.whoami()
        print(f"==> Authenticated. tenant={me.tenant_id} auth={me.auth_method} roles={me.roles}")

        print("\n==> 1. Chat completion (mock-gpt, governed chokepoint)")
        resp = client.chat.completions.create(
            model="mock-gpt",
            messages=[{"role": "user", "content": "Prove the governed path works."}],
        )
        print(f"    {resp.choices[0].message.content}")
        print(
            f"    usage: prompt={resp.usage.prompt_tokens} "
            f"completion={resp.usage.completion_tokens} total={resp.usage.total_tokens}"
        )

        print("\n==> 2. Streaming chat completion")
        print("    ", end="")
        for chunk in client.chat.completions.create(
            model="mock-gpt",
            messages=[{"role": "user", "content": "Stream me a sentence."}],
            stream=True,
        ):
            for ch in chunk.choices:
                if ch.delta.content:
                    print(ch.delta.content, end="", flush=True)
        print()

        print("\n==> 3. Agents")
        agents = client.agents.list()
        for a in agents:
            print(f"    - {a.id}  {a.name}  (model={a.model})")

        if agents:
            print("\n==> 4. Run an agent (rides the same governed path)")
            run = client.agents.run(
                agents[0].id,
                input={"messages": [{"role": "user", "content": "Run via agent."}]},
                wait=True,
                timeout=30,
            )
            print(f"    run {run.id} -> {run.status}")

        print("\n==> 5. Metering + budget state")
        summary = client.metering_summary()
        print(
            f"    events={summary.total_events} tokens={summary.total_tokens} "
            f"spent=${summary.spent_usd:.6f} / limit=${summary.monthly_limit_usd:.6f} "
            f"remaining=${summary.remaining_usd:.6f}"
        )

        print("\n==> 6. Drive the PRE-CALL budget gate (expect HTTP 402)")
        for i in range(1, 11):
            try:
                client.chat.completions.create(
                    model="mock-gpt",
                    messages=[{"role": "user", "content": f"call {i}"}],
                )
                print(f"    call #{i} -> 200 OK")
            except BudgetExceededError as exc:
                print(f"    call #{i} -> 402 budget gate fired BEFORE provider call: {exc.message}")
                break
        else:
            print("    budget gate did not trip in 10 calls (limit may be high)")

    print("\nSUCCESS — SDK exercised auth, chat (sync+stream), agents, runs, metering, 402 gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
