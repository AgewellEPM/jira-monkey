"""Loopback-only Windows protocol fixture; never opens a guest or native UI."""
import json
from pathlib import Path
import sys

from mcp.server import MCPServer
from test_windows_coordinator import ProtocolFixture

server = MCPServer('Monkey Windows protocol fixture — no native execution')
fixture = ProtocolFixture()
ledger = Path(sys.argv[1])


async def invoke(name, arguments):
    if name == 'vcard_run_step':
        fixture.workflow_stage = {'ready': 'awaiting-provider', 'awaiting-provider': 'cleanup-required',
                                  'cleanup-required': 'completed'}[fixture.workflow_stage]
    result = await fixture.call_tool(name, arguments)
    ledger.write_text(json.dumps({'synthetic': True, 'native_actions': 0, 'calls': fixture.calls}, indent=2)+'\n')
    return json.loads(result.value['content'][0]['text'])


@server.tool()
async def legacy_session_catalog() -> dict:
    return await invoke('legacy_session_catalog', {})


@server.tool()
async def legacy_session_open(targetId: str, mode: str, purpose: str, idempotencyKey: str) -> dict:
    return await invoke('legacy_session_open', locals())


@server.tool()
async def legacy_session_status(sessionId: str) -> dict:
    return await invoke('legacy_session_status', locals())


@server.tool()
async def legacy_session_close(sessionId: str, expectedStateDigest: str, purpose: str) -> dict:
    return await invoke('legacy_session_close', locals())


@server.tool()
async def legacy_accessibility_tree(limit: int) -> dict:
    return await invoke('legacy_accessibility_tree', locals())


@server.tool()
async def vcard_status() -> dict:
    return await invoke('vcard_status', {})


@server.tool()
async def vcard_run_start(workflowDigest: str, dryRunDigest: str, inputEnvelopePath: str, inputExpectedSHA256: str) -> dict:
    return await invoke('vcard_run_start', locals())


@server.tool()
async def vcard_run_status(runId: str) -> dict:
    return await invoke('vcard_run_status', locals())


@server.tool()
async def vcard_run_step(runId: str, expectedStateDigest: str) -> dict:
    return await invoke('vcard_run_step', locals())


@server.tool()
async def vcard_provider_tick(runId: str, expectedStateDigest: str) -> dict:
    return await invoke('vcard_provider_tick', locals())


@server.tool()
async def vcard_cleanup_tick(runId: str, expectedStateDigest: str) -> dict:
    return await invoke('vcard_cleanup_tick', locals())


@server.tool()
async def vcard_run_pause(runId: str, expectedStateDigest: str) -> dict:
    return await invoke('vcard_run_pause', locals())


@server.tool()
async def vcard_run_receipt(runId: str) -> dict:
    return await invoke('vcard_run_receipt', locals())


if __name__ == '__main__':
    server.run(transport='streamable-http', host='127.0.0.1', port=int(sys.argv[2]))
