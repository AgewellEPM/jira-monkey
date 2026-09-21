"""Network server dependencies, loaded off the prompt loop on /api start."""
from contextlib import contextmanager

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse
import uvicorn


class LocalServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The REPL owns foreground cancellation and shutdown.
        yield
