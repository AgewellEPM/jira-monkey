"""Bounded Ollama chat assembly. Provisional bytes never become tool decisions."""
import hashlib
import time

from .common import Refused, decode, identity, require


class IncompleteStream(Refused):
    """Valid provisional frames reached EOF without a completion record.

    No generated action is usable. A worker may request a fresh proposal inside
    its original infrastructure budget. This is not a content/schema refusal.
    """


MAX_WIRE_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 128 * 1024
MAX_FRAMES = 16384
MAX_CONTENT_CHARS = 30000


class ChatStream:
    def __init__(self, audit, model):
        self.audit, self.model = audit, model
        self.id = identity('modelstream_')
        self.started = time.monotonic()
        self.first_output_ms = None
        self.last_checkpoint = None
        self.frames = self.wire_bytes = self.content_bytes = self.thinking_bytes = 0
        self.content_chars = 0
        self.wire_hash = hashlib.sha256()
        self.content_hash = hashlib.sha256()
        self.thinking_hash = hashlib.sha256()
        self.parts = []
        self.final = None
        self.returned_model = None
        self.eof = False
        self.observe('model.stream_started', {'requested_model': model,
            'wire_limit_bytes': MAX_WIRE_BYTES, 'content_limit_characters': MAX_CONTENT_CHARS,
            'provisional_output': 'hashes and byte counts only; never executable or displayed as a completed response'})

    def metrics(self):
        return {'stream_id': self.id, 'frames': self.frames, 'wire_bytes': self.wire_bytes,
            'wire_prefix_sha256': self.wire_hash.hexdigest(),
            'content_bytes': self.content_bytes, 'content_sha256': self.content_hash.hexdigest(),
            'thinking_bytes': self.thinking_bytes, 'thinking_sha256': self.thinking_hash.hexdigest(),
            'first_output_ms': self.first_output_ms,
            'elapsed_ms': (time.monotonic() - self.started) * 1000,
            'done_received': self.final is not None, 'eof_received': self.eof,
            'done_reason': self.final.get('done_reason') if self.final else None}

    def observe(self, kind, data):
        if self.audit:
            return self.audit.observe(kind, {'stream_id': self.id, **data})

    def settle(self, outcome, error_type=None):
        self.observe('model.stream_settled', {**self.metrics(), 'outcome': outcome,
            'error_type': error_type, 'complete_response_usable': outcome == 'validated',
            'usage': {field: self.final.get(field) if self.final else None for field in
                ('prompt_eval_count', 'eval_count', 'total_duration', 'load_duration')}})

    def frame(self, raw):
        require(len(raw) <= MAX_FRAME_BYTES, 'Local model stream frame exceeds its bound')
        if not raw.strip():
            return
        require(self.final is None, 'Local model sent data after its final response')
        value = decode(raw)
        require(type(value) is dict and not value.get('error'), 'Local model stream returned an error or invalid frame')
        require(type(value.get('done')) is bool, 'Local model stream frame has no completion flag')
        self.frames += 1
        require(self.frames <= MAX_FRAMES, 'Local model stream has too many frames')
        returned = value.get('model')
        if returned is not None:
            require(type(returned) is str and returned and
                (self.returned_model is None or self.returned_model == returned),
                'Local model identity changed during its response')
            self.returned_model = returned
        message = value.get('message', {})
        require(type(message) is dict and message.get('role', 'assistant') == 'assistant',
            'Local model stream returned an invalid message')
        require(not message.get('tool_calls') and not message.get('images'),
            'Local model stream cannot grant native tool or image-output authority')
        content, thinking = message.get('content', ''), message.get('thinking', '')
        require(type(content) is str and type(thinking) is str, 'Local model stream content must be text')
        raw_content, raw_thinking = content.encode(), thinking.encode()
        self.content_chars += len(content)
        self.content_bytes += len(raw_content)
        self.thinking_bytes += len(raw_thinking)
        self.content_hash.update(raw_content)
        self.thinking_hash.update(raw_thinking)
        require(self.content_chars <= MAX_CONTENT_CHARS and self.thinking_bytes <= MAX_FRAME_BYTES,
            'Local model generated text exceeds its bound')
        self.parts.append(content)
        if (content or thinking) and self.first_output_ms is None:
            self.first_output_ms = (time.monotonic() - self.started) * 1000
        if value['done']:
            self.final = value

    def checkpoint(self, activity):
        if not self.frames:
            return
        tick = time.monotonic()
        if self.last_checkpoint is not None and tick - self.last_checkpoint < 2:
            return
        metrics = self.metrics()
        entry = self.observe('model.output_progress', {**metrics, 'provisional': True})
        # Publish only after the matching signed journal entry has committed.
        activity.update({'state': 'receiving provisional output', **metrics,
            'evidence_seq': entry['seq'] if entry else None})
        self.last_checkpoint = tick

    async def read(self, response, activity):
        pending = bytearray()
        async for chunk in response.aiter_bytes():
            # Hash only the retained bounded prefix if the provider floods us.
            room = max(0, MAX_WIRE_BYTES - self.wire_bytes)
            self.wire_hash.update(chunk[:room])
            self.wire_bytes += len(chunk)
            require(self.wire_bytes <= MAX_WIRE_BYTES, 'Local model stream exceeds its wire bound')
            pending.extend(chunk)
            lines = pending.split(b'\n')
            pending = lines.pop()
            for raw in lines:
                self.frame(bytes(raw))
                self.checkpoint(activity)
            require(len(pending) <= MAX_FRAME_BYTES, 'Local model stream frame exceeds its bound')
        if pending:
            self.frame(bytes(pending))
            self.checkpoint(activity)
        self.eof = True
        if self.final is None:
            raise IncompleteStream('Local model stream ended without a final response')
        return {**self.final, 'model': self.returned_model,
            'message': {'role': 'assistant', 'content': ''.join(self.parts)}}
