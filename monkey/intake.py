"""Recognize direct local drafting requests without granting remote authority."""
import re


def general_request(text):
    if len(text.encode())>6000: return None
    request=re.sub(r'^\s*(?:(?:please|can you|could you|help me|i want to|i need you to)\s+)?','',text,flags=re.I)
    if re.match(r'(?:research|investigate|look up|compare)\s+\S',request,re.I): return 'research'
    if re.match(r'(?:build|code|implement|debug)\s+\S',request,re.I): return 'build'
    if re.match(r'(?:create|make|write|fix)\s+\S',request,re.I) and re.search(r'\b(?:app|application|program|script|website|module|function|library|code|api|cli)\b',request,re.I): return 'build'
    return None


def is_request(text):
    if len(text.encode()) > 6000 or re.search(r"\b[A-Z][A-Z0-9_]*-[0-9]+\b", text):
        return False  # Existing exact identifiers follow the normal target resolver.
    start = re.match(r"\s*(?:(?:please|can you|could you|help me|i want to|i need you to)\s+)?(?:draft|write|create|prepare|make|i need|i want|help with)\b", text, re.I)
    return bool(start and re.search(r"\b(?:tickets?|sprints?|user stor(?:y|ies)|support request|bug report|feature request|acceptance criteria|backlog|task request)\b", text, re.I))
