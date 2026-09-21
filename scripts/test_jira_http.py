"""Controlled loopback Jira-like server. Never contacts a Jira site."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from monkey.adapters import HTTP, Jira
from monkey.app import App
from monkey.common import clone
from monkey.demo import DemoModels, TICKET


class Server:
    def __init__(self, scenario):
        self.scenario = scenario
        self.comments = [{"id": "900", "author": {"accountId": "someone-else"}, "body": {},
                          "properties": [{"key": "unrelated", "value": {}}]}]
        self.posts = 0
        self.requests = []
        self.handlers = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            while True:
                try:
                    head = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                lines = head.decode().split("\r\n")
                method, path, _ = lines[0].split()
                headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
                body = await reader.readexactly(int(headers.get("Content-Length", headers.get("content-length", "0"))))
                self.requests.append({"method": method, "path": path})
                parsed = urlsplit(path)
                status = 200
                if parsed.path == "/rest/api/3/myself":
                    result = {"accountId": "fixture-operator"}
                elif parsed.path.endswith("/issue/DEMO-123"):
                    result = {"id": "42", "key": "DEMO-123", "fields": {"summary": TICKET["title"], "description": TICKET["body"], "updated": TICKET["revision"]}}
                elif parsed.path.endswith("/comment") and method == "POST":
                    self.posts += 1
                    result = {"id": str(10000+self.posts), "author": {"accountId": "fixture-operator"}, **json.loads(body)}
                    self.comments.append(result)
                    if self.scenario == "response-loss":
                        return  # Actual socket loss after commit; no HTTP response.
                    status = 201
                elif parsed.path.endswith("/comment"):
                    start = int(parse_qs(parsed.query).get("startAt", ["0"])[0])
                    result = {"startAt": start, "total": len(self.comments), "comments": self.comments[start:start+1]}
                elif "/comment/" in parsed.path:
                    if self.scenario == "readback-error":
                        status, result = 503, {"error": "fixture read-back failure"}
                    else:
                        cid = parsed.path.rsplit("/", 1)[-1]
                        result = next(c for c in self.comments if c["id"] == cid)
                else:
                    status, result = 404, {"error": "unrecognized fixture path"}
                data = json.dumps(result).encode()
                writer.write(f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)


class LoopbackTransport(httpx.AsyncBaseTransport):
    def __init__(self, port):
        self.port = port
        self.native = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        assert request.url.host == "example.invalid"
        rewritten = httpx.Request(request.method, request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port),
                                  headers=request.headers, content=await request.aread())
        return await self.native.handle_async_request(rewritten)

    async def aclose(self):
        await self.native.aclose()


class FixtureJira(Jira):
    fixture = True


async def scenario(name):
    fixture = Server(name)
    server = await asyncio.start_server(fixture.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix="monkey-http-", dir="/private/tmp") as root:
            http = HTTP(LoopbackTransport(port))
            app = App(root, http=http, models=DemoModels(delay=.001), offline=True,
                      jira_factory=lambda c: FixtureJira(c, http, {"JIRA_API_TOKEN": "loopback-fixture-only"}))
            try:
                app.db.configure({"provider": "ollama", "model": "fixture", "ollama_model": "fixture", "chat_model": "fixture",
                                  "site": TICKET["instance"], "email": "fixture@example.invalid", "max_transport_retries": 0})
                jid = (await app.dispatch("fetch", key="DEMO-123"))["job_id"]
                await app.dispatch("run", target=jid)
                await app.worker.task
                await app.dispatch("approve", target=jid, note="Explicit loopback fixture approval")
                await app.dispatch("publish", target=jid, confirm="DEMO-123")
                first = app.db.job(jid)["delivery_state"]
                await app.dispatch("reconcile", target=jid)
                final = app.db.job(jid)["delivery_state"]
                assert fixture.posts == 1
                assert (first, final) == (("POST_UNKNOWN", "POSTED_VERIFIED") if name == "response-loss" else ("POSTED_UNVERIFIED", "POSTED_UNVERIFIED"))
                return {"scenario": name, "states": [first, final], "post_count": fixture.posts, "requests": fixture.requests,
                        "real_http_on_loopback": True, "live_jira_contacted": False}
            finally:
                await app.close()
    finally:
        server.close()
        await server.wait_closed()
        for task in list(fixture.handlers):
            task.cancel()
        await asyncio.gather(*fixture.handlers, return_exceptions=True)
        assert not server.is_serving()


async def main():
    results = [await scenario(name) for name in ("response-loss", "readback-error")]
    report = {"results": results, "server_cleanup_verified": True}
    (ROOT / "docs/jira-http-integration.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


asyncio.run(main())
