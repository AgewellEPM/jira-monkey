"""Foreground adapter to Kist's real, durable, approval-gated SML runtime.

Only a host-selected callback crosses the local capability bridge. The wire has
no tool arguments, paths, model authority or arbitrary command interface.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sys

import httpx

from .common import Refused, digest, encoded, require


def put(path, value):
    from .platform_files import write_private
    write_private(path,encoded(value))


def file_hash(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def environment():
    # Do not pass provider keys, Jira tokens, DYLD hooks or Python startup hooks.
    result={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
            "HOME": str(Path.home()), "TERM": "dumb"}
    if sys.platform=='darwin':
        # Avoid implicit user-home and timezone-service lookups during framework
        # initialization. UTC0 is a POSIX conversion rule, not a zoneinfo file
        # name; Apple's libc skips file notifications for a parsed rule.
        # The sandbox's IPC permissions stay unchanged.
        # github.com/apple-oss-distributions/webdavfs/blob/main/WebDAVPlugin/WebDAV_Mount.c
        # github.com/apple-oss-distributions/Libc/blob/main/stdtime/FreeBSD/localtime.c
        result.update(__CF_USER_TEXT_ENCODING=f'0x{os.getuid():X}:0:0',TZ='UTC0')
    return result


async def stop(process):
    if process is None or process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


class SML:
    def __init__(self, binary, pin, audit=None):
        self.binary, self.pin = str(binary), pin
        self.audit = audit

    async def execute(self, root, callback, before, *, operation_id):
        if self.audit: self.audit.observe('sml.requested',{'operation_id':operation_id,'binary':self.binary,'binary_sha256':self.pin,'receipt_directory':str(root)})
        require(await asyncio.to_thread(file_hash, self.binary) == self.pin,
                "Kist runtime changed; inspect and authorize a fresh plan")
        root = Path(root)
        root.mkdir(parents=True, mode=0o700)
        host = root / ".kist/sml"
        host.mkdir(parents=True, mode=0o700)
        secret = secrets.token_urlsafe(32)
        task_id = "TASK-" + operation_id
        process = None
        handlers = set()
        response = None
        performed = False
        refusal = None

        async def admitted():
            result = before()
            if inspect.isawaitable(result):
                await result

        def snapshot():
            path = host / "runtime.json"
            require(path.is_file() and not path.is_symlink(), "Missing durable SML state")
            value = json.loads(path.read_bytes())
            require(value.get("schema_version") == 3, "Unsupported SML receipt schema")
            return value

        def task_receipts(value):
            task = value.get("tasks", {}).get(task_id)
            require(task and task["workflow_id"] == "monkey.operation", "Wrong SML task")
            receipts = [value["receipts"][rid] for rid in task["receipt_ids"]]
            require(all(r["task_id"] == task_id and r["workflow_id"] == task["workflow_id"] and
                        r["workflow_version"] == task["workflow_version"] for r in receipts),
                    "Mismatched SML receipts")
            return task, receipts

        async def handle(reader, writer):
            nonlocal response, performed, refusal
            current = asyncio.current_task()
            handlers.add(current)
            code, body = 403, b'{"error":"refused"}'
            authenticated = False
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
                require(len(raw) < 8192, "Oversized local request")
                lines = raw.decode("ascii").split("\r\n")
                require(lines[0] == "POST /perform HTTP/1.1", "Wrong local capability")
                headers = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    key, value = line.split(":", 1)
                    require(key.lower() not in headers, "Duplicate header")
                    headers[key.lower()] = value.strip()
                require(hmac.compare_digest(headers.get("authorization", ""), "Bearer " + secret), "Local capability authority missing")
                authenticated = True
                require(headers.get("content-length", "0") == "0" and "transfer-encoding" not in headers,
                        "This capability accepts no caller-supplied arguments")
                require(not performed, "Operation already dispatched; no replay")
                task, receipts = task_receipts(snapshot())
                dispatch = task.get("capability_claim", {}).get("dispatch_id")
                require(dispatch and task["current_instruction_id"] == "perform", "No durable SML claim")
                claim = receipts[-1]
                require(claim["kind"] == "capability_claimed" and claim["payload"]["driver_id"] == "sml-http-driver" and
                        claim["payload"]["dispatch_id"] == dispatch, "Wrong SML claim")
                require(any(r["kind"] == "approved" and r["payload"].get("owner") == "host-operator" for r in receipts), "SML approval absent")
                await admitted()
                performed = True
                response = await callback()
                body = encoded(response)
                require(len(body) <= 60000, "Capability result exceeds receipt limit")
                code = 200
            except Exception as exc:
                if authenticated:
                    refusal = str(exc) if isinstance(exc, Refused) else type(exc).__name__
            finally:
                writer.write(f"HTTP/1.1 {code} {'OK' if code == 200 else 'Forbidden'}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
                with contextlib.suppress(ConnectionError):
                    await writer.drain()
                writer.close()
                with contextlib.suppress(ConnectionError):
                    await writer.wait_closed()
                handlers.discard(current)

        server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=8192)
        port = server.sockets[0].getsockname()[1]
        try:
            put(host / "authority.json", {"schema_version": 1, "actors": [{
                "id": "sml-runtime-exporter", "roles": ["workflow-agent"],
                "operations": ["runtime.status", "workflow.inspect", "workflow.run", "task.watch", "task.approve_request", "task.claim_capability", "task.record_step", "receipts.list"],
                "approval_authorities": ["host-operator"], "scope_template": {}}]})
            put(host / "catalog.json", {"schema_version": 1, "tool_capabilities": ["monkey.perform"], "approval_authorities": ["host-operator"]})
            put(host / "http-auth.json", {"schema_version": 1, "credentials": {"monkey.session": {"value": secret}}})
            workflow = {"ir_version": 1, "id": "monkey.operation", "version": "1.0.0", "name": "Monkey exact operation",
                "source": {"kind": "native", "reference": operation_id}, "trigger": {"kind": "manual"},
                "state_schema": [{"name": "result", "type": "string", "required": False}],
                "interface": {"input_fields": [], "output_fields": ["result"]}, "entrypoint": "perform",
                "instructions": [{"id": "perform", "op": "CALL_TOOL", "capability": "monkey.perform", "arguments": {}, "next": "receipt"},
                                 {"id": "receipt", "op": "EMIT_RECEIPT", "receipt_fields": ["result"]}]}
            put(host / "workflow.json", workflow)
            put(host / "http-endpoints/monkey.json", {"schema_version": 1, "api_id": "monkey", "source_digest": digest(workflow), "endpoints": [{
                "capability": "monkey.perform", "method": "POST", "path_template": "/perform",
                "servers": [{"scheme": "http", "host": "127.0.0.1", "port": port, "base_path": ""}],
                "parameters": [], "authentication": [{"schemes": [{"credential_id": "monkey.session", "kind": "bearer", "required_scopes": []}]}],
                "response_state_field": "result", "timeout_seconds": 60, "maximum_response_bytes": 60000}]})
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                api_port = reservation.getsockname()[1]
            process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).with_name("runtime_child.py")), self.binary, "--project", str(root), "--sml-runtime-serve", ".kist/sml/workflow.json",
                "--sml-port", str(api_port), "--sml-api-base", "/monkey", "--sml-api-workflow-path", "monkey.operation=/operation",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=environment(), start_new_session=True)
            token_path = host / "serve/session-token"
            async with asyncio.timeout(8):
                while not token_path.exists():
                    require(process.returncode is None, "SML host failed to start")
                    await asyncio.sleep(0.03)
            token = token_path.read_text().strip()
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}",
                    headers={"Authorization": "Bearer " + token}, trust_env=False,
                    follow_redirects=False, timeout=65) as client:
                async with asyncio.timeout(8):
                    while True:
                        try:
                            ready = await client.get("/approvals")
                            require(ready.status_code == 200, "SML session authentication failed")
                            break
                        except httpx.ConnectError:
                            require(process.returncode is None, "SML host failed to bind its port")
                            await asyncio.sleep(0.03)
                created = await client.post("/monkey/operation/tasks", json={"task_id": task_id, "input": {}})
                require(created.status_code < 300, "SML refused task creation")
                task, receipts = task_receipts(snapshot())
                require(task["status"] == "waiting_approval", "SML did not park at its approval gate")
                await admitted()
                approved = await client.post("/monkey/operation/tasks/" + task_id + "/approve", json={"task_revision": task["task_revision"]})
                require(approved.status_code < 300, "SML refused the exact task approval")
                async with asyncio.timeout(65):
                    while snapshot()["tasks"][task_id]["status"] not in {"completed", "failed", "escalated"}:
                        require(refusal is None, "SML tool boundary refused: " + str(refusal))
                        await asyncio.sleep(0.03)
            value = snapshot()
            task, receipts = task_receipts(value)
            require(task["status"] == "completed" and performed and response is not None, "SML capability is incomplete or uncertain; no automatic replay")
            require(task["state"].get("result") == encoded(response).decode(), "SML result does not match host-observed output")
            steps = [r for r in receipts if r["kind"] == "step_recorded"]
            require(len(steps) == 1 and steps[0]["payload"].get("outcome") == "passed" and
                    steps[0]["payload"].get("driver_id") == "sml-http-driver" and
                    steps[0]["payload"].get("evidence", {}).get("authority") == "observed", "Missing observed SML completion")
            require(receipts[-1]["kind"] == "receipt_emitted" and receipts[-1]["payload"].get("result") == encoded(response).decode(), "Missing SML result receipt")
            await stop(process)
            transcript = await process.stdout.read(100000)
            put(root / "transcript.json", {"text": transcript.decode(errors="replace")})
            if self.audit: self.audit.observe('sml.completed',{'operation_id':operation_id,'runtime_path':str(host/'runtime.json'),
                'runtime_sha256':file_hash(host/'runtime.json'),'transcript_sha256':file_hash(root/'transcript.json'),'result_hash':digest(response)})
            return {"runtime": "kist-durable-sml", "binary_hash": self.pin, "task_id": task_id,
                    "runtime_hash": file_hash(host / "runtime.json"), "runtime_path": str(host / "runtime.json"),
                    "workflow_hash": digest(workflow), "receipts": receipts, "result": response}
        finally:
            server.close()
            await server.wait_closed()
            for handler in list(handlers):
                handler.cancel()
            if handlers:
                await asyncio.gather(*list(handlers), return_exceptions=True)
            await stop(process)
            # Ephemeral bridge credentials are never needed for recovery.
            with contextlib.suppress(FileNotFoundError):
                (host / "http-auth.json").unlink()
            if self.audit: self.audit.observe('sml.stopped',{'operation_id':operation_id,'pid':process.pid if process else None,
                'exit_code':process.returncode if process else None,'ephemeral_credentials_removed':not (host/'http-auth.json').exists()})
