"""Bounded wire observation for private qualification fixtures, never work dispatch.

The real HTTPX stream is forwarded byte for byte. Provisional text is retained
only as diagnostic fixture data; the application's completion checks still own
whether a response can become a tool proposal.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def attach(client, root):
    root = Path(root)
    root.mkdir(mode=0o700)
    requests = {}
    limit = 512 * 1024
    ordinal = 0

    async def request_hook(request):
        nonlocal ordinal
        url = urlsplit(str(request.url))
        if request.method != 'POST' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.path != '/api/chat':
            return
        ordinal += 1
        prefix = root / ('request-%03d' % ordinal)
        body = request.content
        requests[id(request)] = prefix
        prefix.with_suffix('.json').write_bytes(body[:limit])
        prefix.with_suffix('.request-receipt.json').write_text(json.dumps({
            'bytes': len(body), 'retained_bytes': min(limit, len(body)),
            'sha256': hashlib.sha256(body).hexdigest(), 'credentials': 'headers not retained',
            'purpose': 'Private qualification fixture; no tool execution by this observer'}, indent=2)+'\n')

    class ObservedStream(httpx.AsyncByteStream):
        def __init__(self, original, prefix):
            self.original, self.prefix = original, prefix
            self.file = prefix.with_suffix('.ndjson').open('xb')
            self.total = self.retained = 0
            self.hasher = hashlib.sha256()
            self.complete = False

        async def __aiter__(self):
            async for chunk in self.original:
                self.total += len(chunk)
                self.hasher.update(chunk)
                kept = chunk[:max(0, limit-self.retained)]
                self.file.write(kept)
                self.retained += len(kept)
                yield chunk
            self.complete = True

        async def aclose(self):
            try:
                await self.original.aclose()
            finally:
                if not self.file.closed:
                    self.file.close()
                    self.prefix.with_suffix('.response-receipt.json').write_text(json.dumps({
                        'bytes_seen': self.total, 'retained_bytes': self.retained,
                        'observed_prefix_sha256': self.hasher.hexdigest(), 'eof': self.complete,
                        'truncated': self.total > self.retained,
                        'meaning': 'Observed wire bytes; EOF alone does not prove model completion'}, indent=2)+'\n')

    async def response_hook(response):
        prefix = requests.pop(id(response.request), None)
        if prefix:
            response.stream = ObservedStream(response.stream, prefix)

    client.event_hooks['request'].append(request_hook)
    client.event_hooks['response'].append(response_hook)
