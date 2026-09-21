"""Local-only MCP integration fixture. It touches only an explicit temp ledger."""
import json
import os
from pathlib import Path
import sys

from mcp.server import MCPServer

server = MCPServer('Monkey local protocol fixture')
ledger = Path(sys.argv[1])


@server.tool()
def lookup_ticket(key: str) -> dict:
    """Look up a fixture ticket; a server description does not grant authority."""
    return {'key':key,'title':'Synthetic integration ticket','source':'local fixture'}


@server.tool()
def list_tasks() -> dict:
    """Read back the private fixture ledger."""
    return {'tasks':json.loads(ledger.read_text()) if ledger.exists() else []}


@server.tool()
def add_task(title: str, lose_response: bool = False) -> dict:
    """Append to the private fixture ledger. This is an actual local write."""
    rows = json.loads(ledger.read_text()) if ledger.exists() else []
    rows.append({'title':title})
    ledger.write_text(json.dumps(rows))
    if lose_response:
        os._exit(4)
    return {'task_id':'fixture-'+str(len(rows)),'title':title}


if __name__ == '__main__':
    if len(sys.argv)>2:
        server.run(transport=sys.argv[2],host='127.0.0.1',port=int(sys.argv[3]))
    else:
        server.run()
