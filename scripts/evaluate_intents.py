"""Run frozen intent labels on an installed local model; no command dispatch."""
import argparse
import asyncio
import collections
import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from intent_cases import cases
from monkey.app import App
from monkey.common import Refused, digest
from monkey.conversation import QUERY_OPS, action_authority
from monkey.demo import TICKET


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values)-1, int((len(values)-1)*q))] if values else None


async def main(args):
    rows = []
    if args.heldout:
        from intent_heldout import cases as heldout_cases
        fixture = heldout_cases()
    else:
        fixture = cases()
    with tempfile.TemporaryDirectory(prefix="monkey-intents-", dir="/private/tmp") as root:
        app = App(root)
        try:
            app.db.configure({"provider": "ollama", "model": args.model, "ollama_model": args.model, "chat_model": args.model})
            first = app.db.add(TICKET, issue_id="42")
            app.db.add({**TICKET, "key": "DEMO-148", "title": "Another login report"}, issue_id="43")
            catalog = await app.models.catalog(app.db.config())
            model_digest = next(r["digest"] for r in catalog if r["name"] == args.model)
            for case in fixture[:args.limit]:
                app.focus = first["id"] if case["context"] == "focused" else None
                started = time.perf_counter()
                raw, meta, error, actual, target = None, {}, None, "clarify", None
                schema_valid = False
                try:
                    raw, meta = await app.conversation.interpret(case["text"], use_fast=args.hybrid)
                    schema_valid = True
                    action_authority(case["text"], raw)
                    actual = raw["operation"]
                    import re
                    targeted_global = actual in {"status", "jobs", "completed", "needs_you"} and bool(re.search(r"\b(?:that|this|selected|current) (?:job|ticket|one)\b|\b[A-Z][A-Z0-9_]*-[0-9]+\b", case["text"]))
                    if targeted_global or actual not in {"status", "jobs", "needs_you", "completed", "caps", "models", "help", "work", "clarify"}:
                        job = app.conversation.target(raw, case["text"])
                        target = job["key"]
                except Refused as exc:
                    actual, error = "clarify", str(exc)
                correct = actual == case["expected"]
                if case["after"] and raw:
                    correct = correct and raw["arguments"].get("after") == case["after"]
                rows.append({**case, "actual": actual, "target": target, "proposal": raw, "correct": correct,
                             "schema_valid": schema_valid, "error": error, "latency_ms": (time.perf_counter()-started)*1000, "metrics": meta})
                if len(rows) % 10 == 0:
                    print(json.dumps({"completed": len(rows), "correct": sum(r["correct"] for r in rows), "schema_valid": sum(r["schema_valid"] for r in rows)}), flush=True)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps({"partial": True, "model": args.model, "model_digest": model_digest, "rows": rows}, indent=2))
            elapsed = [r["latency_ms"] for r in rows]
            report = {"model": args.model, "model_digest": model_digest, "platform": platform.platform(), "machine": "Apple M4, 16 GiB",
                      "python": platform.python_version(), "context_tokens": 4096, "output_tokens": 256,
                      "fixture_sha256": digest(fixture), "label_method": "Fixed coding-agent-authored labels before evaluation; not generated or graded by tested model",
                      "interpretation_only": True, "command_dispatch": False, "hybrid": args.hybrid,
                      "heldout": args.heldout,
                      "deterministic_paths": sum(r["metrics"].get("route") == "deterministic" for r in rows),
                      "local_model_paths": sum(r["metrics"].get("route") == "local" for r in rows),
                      "local_generation_non_thinking": "Model does not advertise thinking capability",
                      "schema_valid": sum(r["schema_valid"] for r in rows), "correct": sum(r["correct"] for r in rows), "total": len(rows),
                      "first_request_ms": elapsed[0] if elapsed else None, "subsequent_p50_ms": percentile(elapsed[1:], .5), "subsequent_p95_ms": percentile(elapsed[1:], .95),
                      "groups": {g: {"correct": sum(r["correct"] for r in rows if r["group"] == g), "total": sum(r["group"] == g for r in rows)} for g in {r["group"] for r in rows}},
                      "rows": rows}
            args.output.write_text(json.dumps(report, indent=2))
            print(json.dumps({k:v for k,v in report.items() if k != "rows"}), flush=True)
        finally:
            await app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--heldout", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/intent-evaluation.json")
    asyncio.run(main(parser.parse_args()))
