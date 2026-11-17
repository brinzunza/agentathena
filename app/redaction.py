"""
Keep secrets out of places that get shown, saved or logged.

The agent runs code, reads web pages and opens URLs; what comes back can hold an API key, a token or a password
(a script that prints its environment, a page that quotes a credential, a .env file). That text goes into the chat
history on disk, the step list, the terminal panel and the server log, and the model reads it too. `redact` swaps
anything shaped like a secret for a marker that says what kind it was, so the output still makes sense.

It matches shapes: well-known key formats, `NAME=value` where the name says secret, credentials inside a URL,
private-key blocks. It is a safety net against accidents, not a guarantee: a secret that looks like ordinary text
gets through, and nothing here stops code the user approved from using one.
"""

import re
from typing import Optional

_PLACEHOLDERS = re.compile(r"^(?:x{4,}|\*{3,}|\.{3,}|your[-_ ]|<.*>|none|null|true|false|changeme|example|placeholder|todo)", re.IGNORECASE)

# (what it was, pattern). Order matters: the specific formats go before the general assignments.
_FORMATS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("api key", re.compile(r"\bsk-(?:proj-|svcacct-|or-v1-)?[A-Za-z0-9_\-]{20,}")),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("stripe key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),      # a JWT
]

# Authorization: Bearer abc..., keeping the label
_BEARER = re.compile(r"(?i)\b(authorization\s*[:=]\s*(?:bearer|basic|token)?\s*|bearer\s+)([A-Za-z0-9._~+/=\-]{16,})")

# NAME=value where the name says it is secret. Only the value is replaced.
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_.\-]*(?:api[_-]?key|secret|token|passw(?:or)?d|private[_-]?key|credentials?)[A-Z0-9_.\-]*)"
    r"(\"?\s*[:=]\s*[\"']?)([A-Za-z0-9_\-/+=.~]{8,})"
)

# https://user:password@host
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s/:@]+:)([^\s/@]{3,})(@)")


def _assignment(match: "re.Match") -> str:
    name, joiner, value = match.groups()
    if _PLACEHOLDERS.match(value):
        return match.group(0)
    return f"{name}{joiner}[redacted]"


def redact(text: Optional[str]) -> Optional[str]:
    """`text` with anything shaped like a secret replaced by [redacted ...]. None and empty text come back as they are."""
    if not text:
        return text
    for label, pattern in _FORMATS:
        text = pattern.sub(f"[redacted {label}]", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)}[redacted]", text)
    text = _ASSIGNMENT.sub(_assignment, text)
    return _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}[redacted]{m.group(3)}", text)
