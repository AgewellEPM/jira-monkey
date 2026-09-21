from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from .common import PROVIDERS, clean_text, clone, origin, require

DEFAULT = {
    "provider": "ollama", "model": "jira-monkey-worker:latest", "ollama_model": "jira-monkey-worker:latest",
    "chat_model": "jira-monkey-chat:latest", "chat_digest": "", "ollama_digest": "", "model_digest": "",
    "site": "", "email": "", "review_policy": "human", "max_attempts": 3,
    "max_provider_calls": 12, "max_transport_retries": 2,
    "ollama_url": "http://127.0.0.1:11434", "chat_context": 4096,
    "chat_output": 256, "chat_wait_seconds": 3, "request_timeout": 120,
    "projects": [], "visibility": None, "prompt_version": "monkey-response-v1",
    "timezone": "America/New_York",
    "model_routes": {}, "routing_policy": "ordered",
    "admission_backend": "monkey",
    "build_recipe": None,
    "build_environment": None,
    "agent_max_calls": 32, "agent_max_tools": 96, "agent_max_seconds": 1800, "agent_context": 8192,
    "kist_binary": "", "kist_source": "",
}


def configuration(raw=None):
    raw = raw or {}
    require(type(raw) is dict and not set(raw) - set(DEFAULT), "Unknown configuration fields")
    c = {**clone(DEFAULT), **raw}
    from .model_routing import validate_routes
    validate_routes(c['model_routes'])
    require(c['routing_policy'] in {'ordered','measured'},'Choose ordered or measured model selection')
    require(c['admission_backend'] in {'monkey','kist'},'Choose Monkey or the explicitly configured Kist admission adapter')
    if c['build_environment'] is not None:
        from .build_environment import descriptor_shape
        descriptor_shape(c['build_environment'])
    if c['build_recipe'] is not None:
        from .security import bounded_json
        r = c['build_recipe']
        bounded_json(r, nodes=150)
        require(type(r) is dict and set(r) == {'schema_version','docker','docker_sha256','socket','engine_id',
                    'image_id','supervisor_sha256','executables'} and r['schema_version'] == 1,
                'Use a captured builder recipe')
        require(all(type(r[k]) is str and len(r[k]) < 2000 for k in ('docker','docker_sha256','socket','engine_id','image_id','supervisor_sha256'))
                and type(r['executables']) is dict, 'Invalid builder configuration')
    for field in ("kist_binary", "kist_source"):
        require(type(c[field]) is str and len(c[field]) < 2000 and (not c[field] or Path(c[field]).is_absolute()), "Use an exact absolute runtime path, or leave the optional adapter unconfigured")
    if "ollama_model" in raw and "chat_model" not in raw:
        c["chat_model"] = c["ollama_model"]
    require(c["provider"] in {*PROVIDERS, "ollama"}, "Choose ollama, claude, openai or deepseek")
    for key in ("model", "ollama_model", "chat_model"):
        require(type(c[key]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", c[key]), "Invalid model ID")
    for key in ("chat_digest", "ollama_digest", "model_digest"):
        require(type(c[key]) is str and (not c[key] or re.fullmatch(r"[0-9a-f]{64}", c[key])), "Invalid model digest")
    p = urlsplit(c["ollama_url"])
    require(p.scheme == "http" and p.hostname in {"127.0.0.1", "localhost", "::1"}
            and not p.username and not p.password and not p.path and not p.query and not p.fragment,
            "Local Ollama must use a loopback HTTP origin")
    if c["site"]:
        origin(c["site"])
    clean_text(c["email"], 254, empty=True)
    require(c["review_policy"] in {"human", "queue"}, "Invalid review policy")
    for name, lo, hi in (("max_attempts", 1, 3), ("max_provider_calls", 1, 12),
                          ("max_transport_retries", 0, 2), ("chat_context", 2048, 8192),
                          ("chat_output", 128, 512), ("chat_wait_seconds", 1, 10), ("request_timeout", 5, 180),
                          ('agent_max_calls',4,96),('agent_max_tools',8,256),('agent_max_seconds',60,7200),('agent_context',4096,16384)):
        require(type(c[name]) is int and lo <= c[name] <= hi, "Invalid " + name)
    require(type(c["projects"]) is list and len(c["projects"]) <= 100 and
            all(type(k) is str and re.fullmatch(r"[A-Z][A-Z0-9_]*", k) for k in c["projects"]), "Invalid project scope")
    v = c["visibility"]
    require(v is None or (type(v) is dict and set(v) == {"type", "value"} and
                         v["type"] in {"group", "role"} and type(v["value"]) is str and 0 < len(v["value"]) < 256),
            "Visibility must be null or an exact group/role")
    require(c["prompt_version"] == DEFAULT["prompt_version"], "Unknown prompt version")
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(c["timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        require(False, "Invalid IANA timezone")
    return c


def recipe(c):
    c = configuration(c)
    cloud={model['provider'] for chain in c['model_routes'].values() for model in chain if model['provider']!='ollama'}
    if c['provider']!='ollama': cloud.add(c['provider'])
    return {**c, "allowed_destinations": [c["ollama_url"],*sorted(cloud)]}
