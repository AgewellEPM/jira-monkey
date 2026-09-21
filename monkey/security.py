"""Small host-enforced boundaries for untrusted JSON, schemas and private files."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
from re import _parser as re_parser
import stat

from .common import Refused, require


def bounded_json(value, *, depth=32, nodes=12000):
    pending = [(value, 0)]
    while pending:
        node, level = pending.pop()
        nodes -= 1
        require(nodes >= 0 and level <= depth, 'JSON structure exceeds the supported complexity limit')
        if type(node) is dict:
            require(all(type(k) is str for k in node), 'JSON object keys must be strings')
            pending.extend((v, level + 1) for v in node.values())
        elif type(node) is list:
            pending.extend((v, level + 1) for v in node)
        else:
            require(node is None or type(node) in {str, int, float, bool}, 'Unsupported JSON value')
            require(type(node) is not float or math.isfinite(node), 'Non-finite JSON number')
    return value


def strict_json(data, *, limit=1000000, nodes=12000):
    require(type(data) in {str, bytes, bytearray} and len(data) <= limit, 'JSON exceeds size limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique,
            parse_constant=lambda _: require(False, 'Non-finite JSON number'))
        return bounded_json(value,nodes=nodes)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise Refused('Invalid or excessively nested JSON') from None


def safe_pattern(pattern):
    """Admit only bounded, non-branching regexes; no backtracking programs from peers.

    Fixed repetitions and one variable repetition of a single character atom are
    supported. Variable repetitions require whole-string anchors, so searching
    cannot multiply the work. Complex expressions require a simpler schema.
    """
    require(type(pattern) is str and len(pattern) <= 500, 'Schema pattern exceeds the supported bound')
    try:
        tokens = list(re_parser.parse(pattern, 0))
    except (re.error, OverflowError, RecursionError):
        raise Refused('Invalid schema pattern') from None
    def atom(op, arg):
        if op in {re_parser.LITERAL, re_parser.NOT_LITERAL, re_parser.ANY, re_parser.CATEGORY}:
            return True
        return op == re_parser.IN and all(k in {re_parser.LITERAL, re_parser.RANGE, re_parser.CATEGORY, re_parser.NEGATE} for k, _ in arg)
    variables, width = 0, 0
    for op, arg in tokens:
        if atom(op, arg):
            width += 1
        elif op == re_parser.AT:
            require(arg in {re_parser.AT_BEGINNING, re_parser.AT_END, re_parser.AT_BEGINNING_STRING, re_parser.AT_END_STRING}, 'Unsupported schema pattern assertion')
        elif op in {re_parser.MAX_REPEAT, re_parser.MIN_REPEAT}:
            lo, hi, child = arg
            require(len(child) == 1 and atom(*child[0]), 'Nested or branching schema patterns are refused')
            variables += lo != hi
            width += lo if lo == hi else 1
        else:
            raise Refused('Branching, lookaround and backreference schema patterns are refused')
    require(width <= 1000 and variables <= 1, 'Schema pattern could require excessive backtracking')
    if variables:
        require(tokens and tokens[0][0] == re_parser.AT and tokens[0][1] in {re_parser.AT_BEGINNING, re_parser.AT_BEGINNING_STRING}
            and tokens[-1][0] == re_parser.AT and tokens[-1][1] in {re_parser.AT_END, re_parser.AT_END_STRING}, 'Variable schema patterns must be anchored at both ends')


def schema_guard(schema):
    bounded_json(schema, depth=24, nodes=6000)
    pending = [(schema, (), 0)]
    remaining = 12000
    while pending:
        node, refs, depth = pending.pop()
        remaining -= 1
        require(remaining >= 0 and depth <= 32, 'Schema expansion exceeds the supported complexity limit')
        if type(node) is list:
            pending.extend((v, refs, depth + 1) for v in node)
        elif type(node) is dict:
            require(not {'$dynamicRef', '$recursiveRef'} & set(node), 'Dynamic and recursive schema references are unavailable')
            if '$ref' in node:
                ref = node['$ref']
                require(type(ref) is str and ref.startswith('#/') and ref not in refs, 'Remote or recursive schema references are unavailable')
                target = schema
                for part in ref[2:].split('/'):
                    key = part.replace('~1', '/').replace('~0', '~')
                    require(type(target) is dict and key in target, 'Missing local schema reference')
                    target = target[key]
                pending.append((target, (*refs, ref), depth + 1))
            if type(node.get('pattern')) is str:
                safe_pattern(node['pattern'])
            if type(node.get('patternProperties')) is dict:
                for pattern in node['patternProperties']:
                    safe_pattern(pattern)
            pending.extend((v, refs, depth + 1) for v in node.values())


@contextmanager
def private_parent(path):
    """Walk directories by descriptor: no linked ancestor, check the final owner/mode."""
    path = Path(path).absolute()
    require('..' not in path.parts, 'Parent traversal in private path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=fd)
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        meta = os.fstat(fd)
        require(meta.st_uid == os.getuid() and stat.S_IMODE(meta.st_mode) == 0o700, 'Private file directory must be owned by you with mode 0700')
        yield fd, path.name
    finally:
        os.close(fd)


def header_fields(headers):
    require(type(headers) is dict and len(headers) <= 30, 'Invalid bounded HTTP headers')
    names = set()
    for name, value in headers.items():
        require(type(name) is str and re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}", name)
            and type(value) is str and len(value) <= 16000 and all(32 <= ord(c) < 127 for c in value), 'Invalid HTTP header')
        require(name.lower() not in names, 'Duplicate HTTP header identity')
        names.add(name.lower())
    require(not names & {'host', 'content-length', 'transfer-encoding', 'connection', 'upgrade', 'proxy-authorization', 'proxy-connection'}, 'Routing, proxy and framing headers are host-owned')
    return headers
