"""Pin a dashboard request's conversational focus across asynchronous model calls."""
from contextvars import ContextVar

UNBOUND = object()
REQUEST_FOCUS = ContextVar('monkey_request_focus', default=UNBOUND)
