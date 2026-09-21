"""Trusted launcher: apply hard resource limits, then replace ourselves with sandbox-exec."""
import os
import resource
import sys


if __name__ == '__main__':
    for kind, limit in ((resource.RLIMIT_CPU, 30), (resource.RLIMIT_FSIZE, 8 * 1024 * 1024),
                        (resource.RLIMIT_NOFILE, 128), (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (limit, limit))
    os.execv('/usr/bin/sandbox-exec', ['/usr/bin/sandbox-exec', *sys.argv[1:]])
