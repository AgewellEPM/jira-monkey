"""Synthetic, read-only model benchmark: no Jira calls or external provider."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monkey.app import App
from monkey.conversation import INTENT_SCHEMA, INTENT_PROMPT, WIRE_SCHEMA
from monkey.common import object_schema
import httpx


async def main():
    async with httpx.AsyncClient(trust_env=False, timeout=60) as client:
        payload = {"model": "gemma3:4b", "stream": False,
                   "options": {"num_predict": 256, "num_ctx": 4096, "temperature": 0},
                   "messages": [{"role": "system", "content": INTENT_PROMPT},
                                {"role": "user", "content": "wahts monkey doin"}]}
        def bare(shape):
            if isinstance(shape, dict):
                return {k: bare(v) for k, v in shape.items() if k not in {"maxLength", "maxItems"}}
            if isinstance(shape, list):
                return [bare(v) for v in shape]
            return shape
        for shape in (object_schema({"operation": {"type": "string", "maxLength": 128}}), WIRE_SCHEMA, bare(WIRE_SCHEMA)):
            r = await client.post("http://127.0.0.1:11434/api/chat", json={**payload, "format": shape})
            print("SYNTHETIC RAW DIAGNOSTIC", r.status_code, r.text[:2000], flush=True)
    with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
        app = App(root)
        try:
            for text in ["wahts monkey doin", "could you show which tickets are waiting for me?", "work the queue"]:
                try:
                    value, metrics = await app.conversation.interpret(text, use_fast=False)
                    print(json.dumps({"text": text, "proposal": value, "metrics": metrics}), flush=True)
                except Exception as exc:
                    print(type(exc).__name__, str(exc), flush=True)
                    print(json.dumps(app.db.records("provider_calls")[-2:]), flush=True)
        finally:
            await app.close()


asyncio.run(main())
