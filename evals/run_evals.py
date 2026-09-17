"""
LLM eval harness — run against live Vertex + DB + mock backend:
    python -m evals.run_evals

Per case:
  tool_ok      : every expected tool was called
  codes_ok     : every expected error code appears in the answer
  action_ok    : proposed action (or none) matches expectation, including bureau
  grounded     : answer cites a knowledge source when it explains an error code
  judge_score  : LLM-as-judge 1-5 (accurate to trace, no PII, no invented procedure, concise)
Exit 1 if thresholds fail -> CI gate on prompt/model changes.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.agent.runner import investigate
from app.llm.vertex import generate

JUDGE = """Grade this AI answer given to an operations user about a failed credit request.
Tool results the agent saw (ground truth):
{trace}

Answer:
{answer}

Score 1-5 on: (a) every factual claim is supported by the tool results, (b) no borrower personal data,
(c) no procedure stated that is not in the retrieved documentation, (d) clear and concise.
Respond ONLY with JSON: {{"score": 1-5, "issues": ["..."]}}"""


async def run(path: Path) -> dict:
    cases = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    rows = []
    for c in cases:
        out = await investigate(c["question"], user_id="eval-runner")
        called = {t["name"] for t in out["trace"] if t["type"] == "tool_call"}
        proposed = out["proposed_actions"][0] if out["proposed_actions"] else None
        action_ok = (proposed is None and c["expect_action"] is None) or (
            proposed is not None and proposed["action_type"] == c["expect_action"]
            and proposed["parameters"].get("bureau") == c["expect_bureau"])
        ground_truth = json.dumps([t for t in out["trace"] if t["type"] == "tool_result"])[:6000]
        judge = json.loads(await generate(JUDGE.format(trace=ground_truth, answer=out["answer"]), json_mode=True))
        r = {"id": c["id"],
             "tool_ok": set(c["expect_tools"]) <= called,
             "codes_ok": all(code in out["answer"] for code in c["expect_codes"]),
             "action_ok": action_ok,
             "grounded": not c["expect_codes"] or bool(out["citations"]),
             "judge_score": judge.get("score", 0), "issues": judge.get("issues", [])}
        rows.append(r); print(json.dumps(r))
    n = len(rows)
    return {"tool_rate": sum(r["tool_ok"] for r in rows) / n, "codes_rate": sum(r["codes_ok"] for r in rows) / n,
            "action_rate": sum(r["action_ok"] for r in rows) / n, "grounding_rate": sum(r["grounded"] for r in rows) / n,
            "avg_judge": sum(r["judge_score"] for r in rows) / n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", type=Path, default=Path(__file__).parent / "golden_cases.jsonl")
    ap.add_argument("--min-action", type=float, default=1.0, help="action gating must be perfect")
    ap.add_argument("--min-codes", type=float, default=0.8)
    ap.add_argument("--min-judge", type=float, default=3.5)
    a = ap.parse_args()
    m = asyncio.run(run(a.golden)); print(json.dumps(m, indent=2))
    ok = m["action_rate"] >= a.min_action and m["codes_rate"] >= a.min_codes and m["grounding_rate"] == 1.0 and m["avg_judge"] >= a.min_judge
    sys.exit(0 if ok else 1)
