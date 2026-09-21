"""Keep Kist's foreground host tied to the owning Monkey pipe, including crashes."""
import os
import signal
import subprocess
import sys
import threading


def main():
    ended = threading.Event()
    child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
    def closed_pipe():
        try:
            while os.read(0, 1024):
                pass
        finally:
            ended.set()
    threading.Thread(target=closed_pipe, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: ended.set())
    signal.signal(signal.SIGINT, lambda *_: ended.set())
    try:
        while child.poll() is None and not ended.wait(0.05):
            pass
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return child.returncode


if __name__ == '__main__':
    sys.exit(main())
