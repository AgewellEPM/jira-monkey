"""Exercise real installed Ollama inference with a synthetic ticket, no Jira."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from monkey.app import App
from monkey.demo import TICKET


async def main(args):
    with tempfile.TemporaryDirectory(prefix="monkey-live-", dir="/private/tmp") as root:
        app = App(root)
        try:
            app.db.configure({"provider": "ollama", "model": args.model, "ollama_model": args.model, "chat_model": args.model})
            await app.capture_config()
            job = app.db.add(TICKET)
            app.focus = job["id"]
            await app.dispatch("run", target=job["id"])
            measurements = []
            requested_pause = False
            seq = 0
            while not app.worker.task.done():
                for event in app.db.events(seq):
                    seq = event["seq"]
                    if event["data"].get("message"):
                        print(event["kind"], event["data"]["message"], flush=True)
                j = app.db.job(job["id"])
                if j["stage"] == "DRAFT" and not requested_pause:
                    for _ in range(200):
                        started = time.perf_counter()
                        await app.dispatch("status")
                        measurements.append((time.perf_counter()-started)*1000)
                    await app.dispatch("pause", target=job["id"], after="review")
                    requested_pause = True
                await asyncio.sleep(.05)
            await app.worker.task
            j = app.db.job(job["id"])
            report = {"synthetic_ticket": True, "live_local_inference": True, "jira_contacted": False,
                      "model": args.model, "job": app.summary(j), "records": await app.dispatch("show", target=j["id"]),
                      "calls": app.db.records("provider_calls"), "status_samples": len(measurements),
                      "status_p95_ms": sorted(measurements)[int(len(measurements)*.95)] if measurements else None,
                      "pause_after_review_verified": j["work_state"] == "PAUSED" and bool(j["review_id"])}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2))
            print(json.dumps({k:v for k,v in report.items() if k not in {"records", "calls"}}), flush=True)
        finally:
            await app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/live-local-smoke.json")
    asyncio.run(main(parser.parse_args()))
