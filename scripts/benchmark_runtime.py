"""Measure real core/prompt-toolkit paths with in-memory terminal I/O and fakes."""
import asyncio
import datetime as dt
import json
from pathlib import Path
import platform
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from monkey.demo import TICKET, demo_app
from monkey.ui import repl


def p95(values):
    return sorted(values)[int((len(values)-1)*.95)] if values else None


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


async def main():
    events, outputs, latencies = [], [], []
    def capture(*values, **kwargs):
        for value in values:
            try:
                output = json.loads(str(value))
            except ValueError:
                continue
            outputs.append(output)
            if output.get("type") == "event":
                event = output["value"]
                events.append((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(event["created_at"])).total_seconds()*1000)
    with tempfile.TemporaryDirectory(prefix="monkey-benchmark-", dir="/private/tmp") as root:
        app, jira = demo_app(root, delay=.2)
        release=asyncio.Event()
        original_draft=app.models.draft
        async def held_draft(*args):
            app.models.started.set()
            await release.wait()
            return await original_draft(*args)
        app.models.draft=held_draft
        try:
            with create_pipe_input() as pipe:
                session = PromptSession(input=pipe, output=DummyOutput())
                with patch("builtins.print", capture):
                    ui = asyncio.create_task(repl(app, json_mode=True, session=session))
                    await asyncio.sleep(.05)
                    pipe.send_text("/fetch DEMO-123\n")
                    await until(lambda: app.focus is not None)
                    jid = app.focus
                    pipe.send_text("/run " + jid + "\n")
                    await app.models.started.wait()
                    for _ in range(1000):
                        started = time.perf_counter()
                        await app.dispatch("status")
                        latencies.append((time.perf_counter()-started)*1000)
                        # Give the concurrent UI/worker the same scheduling
                        # boundary as independent terminal or HTTP requests.
                        await asyncio.sleep(0)
                    pipe.send_text("/pause " + jid + " --after review\n")
                    await until(lambda: app.db.job(jid)["pause_after"] == "review")
                    pipe.send_text("show teh dr")
                    release.set()
                    await until(lambda: app.db.job(jid)["work_state"] == "PAUSED")
                    await asyncio.sleep(.1)
                    intact = session.default_buffer.text == "show teh dr"
                    pipe.send_text("aft\n")
                    await until(lambda: any(isinstance(x.get("value"), dict) and "candidate" in x["value"] for x in outputs))
                    pipe.send_text("/quit\n")
                    await asyncio.wait_for(ui, 3)
            result = {"machine": "Apple M4, 16 GiB", "platform": platform.platform(), "python": platform.python_version(),
                      "status_samples": len(latencies), "status_p95_ms": p95(latencies),
                      "event_samples": len(events), "event_to_ui_emit_p95_ms": p95(events),
                      "typed_input_survived_concurrent_events": intact, "pause_after_review": app.db.job(jid)["work_state"],
                      "post_count": jira.posts, "model_adapter": "offline fixture",
                      "measurement_boundary": "Core handler and prompt-toolkit input with captured UI emit; physical display paint is not measured"}
            (ROOT / "docs/runtime-benchmark.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result, indent=2))
            assert intact and result["status_p95_ms"] < 100 and result["event_to_ui_emit_p95_ms"] < 250
        finally:
            await app.close()


asyncio.run(main())
