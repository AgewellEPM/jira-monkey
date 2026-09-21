"""Build and invoke a tiny adapter over the installed project's actual Captain."""
import asyncio
import json
from pathlib import Path

from .common import digest, encoded, require
from .sml import environment, file_hash, put, stop

SOURCES = ("CaptainFacts.swift", "CaptainRule.swift", "CaptainRuleSet.swift", "SymbolicCaptain.swift")


def source_manifest(source):
    source = Path(source).expanduser().resolve() / "Sources/AgentCore"
    files = [source / name for name in SOURCES] + [Path(__file__).with_name("captain_main.swift")]
    require(all(f.is_file() and not f.is_symlink() for f in files), "Kist Captain source is unavailable; execution sign-off stays blocked")
    manifest = {str(f): file_hash(f) for f in files}
    return files,manifest


async def build(root, source):
    files,manifest = source_manifest(source)
    folder = Path(root) / "captain" / digest(manifest)
    binary = folder / "monkey-captain"
    if binary.is_file():
        saved = json.loads((folder / "build.json").read_bytes())
        require(saved["binary_hash"] == file_hash(binary) and saved["sources"] == manifest, "Captain adapter changed; rebuild explicitly")
        return saved
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    process = await asyncio.create_subprocess_exec("/usr/bin/xcrun", "swiftc", "-O", *map(str, files), "-o", str(binary),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=environment(), start_new_session=True)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 60)
        require(process.returncode == 0, "Captain adapter build failed: " + output.decode(errors="replace")[-2000:])
    finally:
        await stop(process)
    saved = {"binary": str(binary), "binary_hash": file_hash(binary), "sources": manifest}
    put(folder / "build.json", saved)
    return saved


async def judge(build, facts):
    require(file_hash(build["binary"]) == build["binary_hash"], "Captain binary changed")
    process = await asyncio.create_subprocess_exec(build["binary"], stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=environment(), start_new_session=True)
    try:
        raw, _ = await asyncio.wait_for(process.communicate(encoded(facts)), 5)
        require(process.returncode == 0 and len(raw) < 16384, "Captain judgment unavailable")
        return json.loads(raw)
    finally:
        await stop(process)
