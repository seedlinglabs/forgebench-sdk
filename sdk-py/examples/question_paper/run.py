"""Run the question-paper workflow through the door.

    python run.py [--subject Biology] [--grade 9] [--topics 3] [--board CBSE]

Four agents, each with its own credential (from setup.py), none holding
another's. Three of them run as CALLEES in this process — pull delivery, no
inbound port — and the orchestrator opens tasks on them through the control
plane. Every model call, every tool decision and every task is one governed
call; the whole run hangs under the orchestrator's first call as a tree.

    orchestrator ─ plan (model)                                  ← root
       ├─ task → research × N topics (parallel)
       │     └─ research: model ⇄ tool loop (papers_search/fetch, gated, reported)
       ├─ task → generator  (asks "which board?" → input_required → answered)
       │     └─ generator: model → paper + answer key
       ├─ task → reviewer
       │     └─ reviewer: model → score + issues
       └─ task → generator (revision, if the reviewer scored it low)

Nothing here is staged: with a keyless stack the model is mock-gpt (which
never emits tool_calls, so the research loop returns the model's text) and the
gates, tasks and ledger rows are exactly as real.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # packages/sdk-py

from forgebench import Forgebench, Task, TaskReply  # noqa: E402

STATE = json.loads((HERE / "qp-agents.json").read_text())
BASE = STATE["base_url"]
MODEL = STATE["model"]
AG = STATE["agents"]


def client(role: str) -> Forgebench:
    return Forgebench(api_key=AG[role]["api_key"], base_url=BASE, timeout=90)


def log(role: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {role:<12} {msg}", flush=True)


def text_of(completion) -> str:
    return (completion.choices[0].message.content or "").strip() if completion.choices else ""


def parse_json(s: str, fallback: Any) -> Any:
    """The model was asked for JSON; decode the first object/array it produced,
    fences and prose around it notwithstanding."""
    s = re.sub(r"```(?:json)?", "", s)
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                value, _ = dec.raw_decode(s[i:])
                return value
            except json.JSONDecodeError:
                continue
    return fallback


# ─────────────────────────────────────────────────────── the research callee ──
PAPERS_DB = {
    "photosynthesis": [
        ("p-2023-bio-9", "Explain the role of chlorophyll in photosynthesis. (2 marks)"),
        ("p-2022-bio-9", "Write the balanced equation of photosynthesis and name the products. (3 marks)"),
    ],
    "cell": [
        ("p-2023-bio-9", "Differentiate between plant and animal cells with three points. (3 marks)"),
        ("p-2021-bio-9", "Which organelle is called the powerhouse of the cell and why? (2 marks)"),
    ],
    "tissue": [("p-2022-bio-9", "Name the tissue that connects muscle to bone. (1 mark)")],
}

PAPER_SCHEMAS = {
    "mcp_papers_search": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    },
    "mcp_papers_fetch": {
        "type": "object",
        "properties": {"paper_id": {"type": "string"}},
        "required": ["paper_id"],
    },
}


def papers_execute(name: str, args: dict[str, Any]) -> Any:
    """The research agent's OWN tool runner — what its MCP client would do.
    The control plane gated the decision; this is the execution it reports."""
    if name == "mcp_papers_search":
        q = str(args.get("query", "")).lower()
        hits = [
            {"paper_id": pid, "question": qn, "topic": topic}
            for topic, rows in PAPERS_DB.items()
            if topic in q or q in topic
            for pid, qn in rows
        ]
        return {"hits": hits[: int(args.get("limit", 5) or 5)]}
    if name == "mcp_papers_fetch":
        pid = args.get("paper_id")
        return {"paper_id": pid, "questions": [qn for rows in PAPERS_DB.values() for p, qn in rows if p == pid]}
    raise ValueError(f"unknown tool {name}")


def research_handler(c: Forgebench):
    def handle(task: Task) -> TaskReply:
        topic = task.data.get("topic") or task.text
        log("research", f"task {task.id[:8]} topic={topic!r}")
        tools = c.agent_tools.openai_schema(parameters=PAPER_SCHEMAS)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "You research one syllabus topic for an exam setter. Use the papers tools to find past questions, then reply with JSON: {\"notes\": [str], \"past_questions\": [str]}."},
            {"role": "user", "content": f"Topic: {topic}. Grade {task.data.get('grade', 9)} {task.data.get('subject', 'Biology')}."},
        ]
        used: list[str] = []
        for step in range(4):
            turn = c.chat.completions.create(
                model=MODEL, messages=messages, parent_call_id=task.call_id,
                extra_body={"tools": tools} if tools else None,
            )
            tcs = turn.choices[0].message.tool_calls if turn.choices else None
            if not tcs:
                data = parse_json(text_of(turn), {"notes": [text_of(turn)[:400]], "past_questions": []})
                log("research", f"  done in {step + 1} turn(s); tools used: {used or 'none'}")
                return task.done(data={"topic": topic, "tools_used": used, **data})
            names = [tc["function"]["name"] for tc in tcs]
            used += names
            log("research", f"  model picked {names} (gate allowed) → executing + reporting")
            messages.append({"role": "assistant", "content": turn.choices[0].message.content, "tool_calls": tcs})
            messages += c.agent_tools.dispatch(tcs, papers_execute, call_id=turn.call_id)
        return task.fail("tool loop did not converge")
    return handle


# ─────────────────────────────────────────────────────── the generator callee ──
def generator_handler(c: Forgebench):
    def handle(task: Task) -> TaskReply:
        d = task.data
        if not d.get("board"):
            log("generator", f"task {task.id[:8]} has no board → asking (input_required)")
            return task.ask("Which board is this paper for: CBSE or ICSE?")
        n = int(d.get("questions", 6))
        log("generator", f"task {task.id[:8]} board={d['board']} questions={n} revision={'yes' if d.get('issues') else 'no'}")
        prompt = (
            f"Write a {d.get('grade', 9)} {d.get('subject', 'Biology')} exam section for the {d['board']} board: "
            f"{n} questions across these topics with marks, then an answer key. Notes per topic:\n"
            f"{json.dumps(d.get('notes', []), indent=1)[:3000]}\n"
            + (f"Fix these reviewer issues: {d['issues']}\n" if d.get("issues") else "")
            + "Reply with JSON: {\"questions\": [{\"q\": str, \"marks\": int, \"topic\": str}], \"answer_key\": [str]}."
        )
        turn = c.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": prompt}], parent_call_id=task.call_id, max_tokens=4000,
        )
        paper = parse_json(text_of(turn), {"questions": [{"q": text_of(turn)[:300], "marks": 1, "topic": "?"}], "answer_key": []})
        return task.done(
            artifacts=[
                {"name": "paper", "parts": [{"data": {"questions": paper.get("questions", [])}}]},
                {"name": "answer-key", "parts": [{"data": {"answer_key": paper.get("answer_key", [])}}]},
            ]
        )
    return handle


# ──────────────────────────────────────────────────────── the reviewer callee ──
def reviewer_handler(c: Forgebench):
    def handle(task: Task) -> TaskReply:
        log("reviewer", f"task {task.id[:8]} reviewing {len(task.data.get('questions', []))} questions")
        prompt = (
            "Review this exam section for topic coverage, difficulty spread and ambiguity. "
            "Reply with JSON: {\"score\": 0-10, \"issues\": [str]}.\n" + json.dumps(task.data)[:3000]
        )
        turn = c.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": prompt}], parent_call_id=task.call_id,
        )
        verdict = parse_json(text_of(turn), {"score": 7, "issues": []})
        try:
            verdict["score"] = float(verdict.get("score", 7))
        except (TypeError, ValueError):
            verdict["score"] = 7.0
        return task.done(data=verdict)
    return handle


# ───────────────────────────────────────────────────────────── the orchestrator ──
def orchestrate(subject: str, grade: int, topics_n: int, board: str) -> dict[str, Any]:
    orch = client("orchestrator")
    research, generator, reviewer = AG["research"]["name"], AG["generator"]["name"], AG["reviewer"]["name"]
    tree: dict[str, Any] = {"root": None, "tasks": []}

    def record(task: Task, label: str) -> Task:
        tree["tasks"].append({"label": label, "id": task.id, "call_id": task.call_id, "parent": task.parent_call_id, "root": task.root_call_id, "state": task.state})
        return task

    # 1. Plan — the ROOT of the tree.
    plan = orch.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": f"List {topics_n} {subject} topics for a grade {grade} exam as a JSON array of short strings."}],
    )
    topics = parse_json(text_of(plan), None)
    if not isinstance(topics, list) or not topics:
        topics = ["photosynthesis", "cell structure", "tissues"][:topics_n]
    topics = [str(t) for t in topics[:topics_n]]
    tree["root"] = plan.call_id
    log("orchestrator", f"plan call {plan.call_id} → topics {topics}")

    # 2. Research, fanned out in parallel — every task a child of the plan.
    def research_one(topic: str) -> Task:
        t = orch.agents.call(research, text=f"Research {topic}", data={"topic": topic, "grade": grade, "subject": subject},
                             parent_call_id=plan.call_id, wait=30)
        while not t.terminal and t.state != "input_required":
            t = orch.agents.task(research, t.id, wait=30)
        return record(t, f"research:{topic}")

    with ThreadPoolExecutor(max_workers=len(topics)) as pool:
        results = list(pool.map(research_one, topics))
    notes = []
    for t in results:
        log("orchestrator", f"research task {t.id[:8]} → {t.state}" + (f" ({t.error})" if t.error else ""))
        if t.state == "completed":
            notes.append(next((a["parts"][0]["data"] for a in t.artifacts or [] if a["parts"] and "data" in a["parts"][0]), {}))

    # 3. Generate — the generator asks a question first; answer on the same context.
    gen = orch.agents.call(generator, text="Write the paper", data={"notes": notes, "grade": grade, "subject": subject, "questions": 2 * len(topics)},
                           parent_call_id=plan.call_id, wait=30)
    record(gen, "generator:first")
    if gen.state == "input_required":
        log("orchestrator", f"generator asked: {gen.question!r} → answering {board}")
        gen = orch.agents.call(generator, text=board, data={"notes": notes, "grade": grade, "subject": subject, "board": board, "questions": 2 * len(topics)},
                               context_id=gen.context_id, parent_call_id=plan.call_id, wait=30)
    while not gen.terminal and gen.state != "input_required":
        gen = orch.agents.task(generator, gen.id, wait=30)
    record(gen, "generator:paper")
    paper = (gen.artifact("paper") or {"parts": [{"data": {}}]})["parts"][0].get("data", {})
    key = (gen.artifact("answer-key") or {"parts": [{"data": {}}]})["parts"][0].get("data", {})
    log("orchestrator", f"generator → {gen.state}, {len(paper.get('questions', []))} questions")

    # 4. Review; one revision round if it scores low.
    rev = orch.agents.call(reviewer, text="Review", data=paper, parent_call_id=plan.call_id, wait=30)
    while not rev.terminal:
        rev = orch.agents.task(reviewer, rev.id, wait=30)
    record(rev, "reviewer")
    verdict = next((a["parts"][0]["data"] for a in rev.artifacts or [] if a["parts"] and "data" in a["parts"][0]), {"score": 0, "issues": []})
    log("orchestrator", f"reviewer → score {verdict.get('score')} issues {verdict.get('issues')}")
    if float(verdict.get("score", 0)) < 7 and verdict.get("issues"):
        gen2 = orch.agents.call(generator, text="Revise", data={"notes": notes, "grade": grade, "subject": subject, "board": board, "questions": 2 * len(topics), "issues": verdict["issues"]},
                                context_id=gen.context_id, parent_call_id=plan.call_id, wait=30)
        while not gen2.terminal and gen2.state != "input_required":
            gen2 = orch.agents.task(generator, gen2.id, wait=30)
        record(gen2, "generator:revision")
        if gen2.state == "completed":
            paper = (gen2.artifact("paper") or {"parts": [{"data": {}}]})["parts"][0].get("data", paper)
            key = (gen2.artifact("answer-key") or {"parts": [{"data": {}}]})["parts"][0].get("data", key)
            log("orchestrator", "revision applied")

    tree["paper"] = paper
    tree["answer_key"] = key
    tree["verdict"] = verdict
    return tree


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", default="Biology")
    ap.add_argument("--grade", type=int, default=9)
    ap.add_argument("--topics", type=int, default=3)
    ap.add_argument("--board", default="CBSE")
    args = ap.parse_args()

    print(f"control plane {BASE}  model {MODEL}\n")

    # The three callees, each serving under its OWN credential. Pull delivery:
    # they claim their tasks from the door; nothing listens on a port.
    stop = threading.Event()

    def worker(role: str, factory):
        c = client(role)
        return threading.Thread(target=lambda: c.agents.serve(factory(c), wait=10, stop=stop.is_set), daemon=True)

    # THREE research replicas on one credential: the door's claim is
    # FOR UPDATE SKIP LOCKED, so the three topics are handed out concurrently
    # and no task is handed out twice.
    servers = [worker("research", research_handler) for _ in range(3)] + [
        worker("generator", generator_handler),
        worker("reviewer", reviewer_handler),
    ]
    for s in servers:
        s.start()

    started = time.time()
    try:
        tree = orchestrate(args.subject, args.grade, args.topics, args.board)
    finally:
        stop.set()

    print("\n════ paper ════")
    for i, q in enumerate(tree["paper"].get("questions", []), 1):
        print(f"{i}. [{q.get('marks', '?')}m] {q.get('q')}")
    print("\n════ answer key ════")
    for i, a in enumerate(tree["answer_key"].get("answer_key", []), 1):
        print(f"{i}. {a}")
    print(f"\nreviewer verdict: {json.dumps(tree['verdict'])}")

    print("\n════ what the door saw (call lineage) ════")
    print(f"root  plan call            {tree['root']}")
    for t in tree["tasks"]:
        print(f"  ├─ {t['label']:<22} task {t['id'][:8]}  call {str(t['call_id'])[:8]}  parent {str(t['parent'])[:8]}  root {str(t['root'])[:8]}  {t['state']}")
    print(f"\n{time.time() - started:.1f}s. Cost of this run = SUM(cost_usd) over metering_events WHERE root_call_id = '{tree['root']}'.")
    print("Graph: GET /v1/agents/graph  (declared edges from the manifests, observed edges from these tasks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
