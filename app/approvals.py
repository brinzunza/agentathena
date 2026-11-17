"""
When the agent may act on the user's computer without asking.

Running code and saving files change things outside the conversation, so each use is put to the user in
"ask" mode, and runs without asking in "bypass" mode. Two things were missing from that:

  - A way to say "yes, for this whole chat" instead of answering the same question after every step.
  - A floor under "bypass". Bypass means "stop asking me about ordinary work", not "run anything at all". Code
    that reaches for private keys, deletes whole folders, or changes system settings is put to the user even then,
    and a few things are refused outright however anyone answers.

These checks read the text of the code. They catch what a model slips into code by accident or by being
steered by a web page, not what a determined program can hide: run_python is not a security boundary
(see run_python_code), and nothing here pretends it is.

State is kept in memory, per chat, and ends with the process: "allow for this chat" never outlives a restart.
"""

import re
import threading
from typing import Dict, List, Optional, Set

# --- what is asked about ---

KIND_CODE = "code"
KIND_SAVE = "save"

# --- the ways a call can proceed ---

ASKED = "asked"         # the user was shown it and said yes
CHAT = "chat"           # the user said yes to this kind of call for the whole chat
BYPASS = "bypass"       # the chat is in bypass mode

APPROVAL_NOTES = {
    CHAT: "ran without asking: you allowed this for the chat",
    BYPASS: "ran without asking: this chat is in bypass mode",
}


# --- refused outright, however anyone answers ---

_HARD_BLOCKS = [
    (r"\brm\s+(-\w+\s+)*-\w*[rf]\w*\s+(-\w+\s+)*(/|~|\$HOME)(\s|[\"']|$)", "deleting your whole home folder or the whole disk"),
    (r"rmtree\(\s*[\"'](/|~|/Users/?|/Users/[^/\"']+/?)[\"']", "deleting your whole home folder or the whole disk"),
    (r"\bmkfs(\.\w+)?\b|\bdiskutil\s+(erase|partition|zero)", "erasing or formatting a disk"),
    (r"\bdd\b[^\n]*\bof=/dev/", "writing straight to a disk"),
    (r":\(\)\s*\{\s*:\s*\|\s*:", "a fork bomb"),
]

# --- put to the user even in bypass mode ---

_DANGERS = [
    (r"\.ssh\b|id_rsa|id_ed25519|\.gnupg|\.aws\b|\.kube\b|\.netrc|keychains?\b|\.docker/config|\.config/gcloud",
     "it reaches for private keys or credentials"),
    (r"[\"'/]\.env\b", "it reads a .env file, which may hold API keys"),
    (r"\brm\s+-|shutil\.rmtree|os\.removedirs|\.rmdir\(", "it deletes whole folders"),
    (r"\bsudo\b|\bsu\s+-|\bchmod\b|\bchown\b|\blaunchctl\b|\bcrontab\b|\bosascript\b|\bdefaults\s+write\b|\bpmset\b",
     "it changes system settings or acts as administrator"),
    (r"(curl|wget)\b[^\n|]*\|\s*(ba|z)?sh\b", "it downloads a program and runs it"),
    (r"[\"'](/etc|/System|/Library|/private|/usr/local/bin|/usr/bin|/bin|/sbin|/var)(/|[\"'])", "it touches system folders"),
    (r"\bos\.system\b|\bsubprocess\b|\bpty\.spawn\b|\bos\.exec\w*\b|\bos\.popen\b", "it starts other programs on your computer"),
    (r"\bsocket\.socket\b|\bsmtplib\b|\bftplib\b|\bparamiko\b|\btelnetlib\b", "it opens network connections of its own"),
]

_COMPILED_BLOCKS = [(re.compile(pattern, re.IGNORECASE), why) for pattern, why in _HARD_BLOCKS]
_COMPILED_DANGERS = [(re.compile(pattern, re.IGNORECASE), why) for pattern, why in _DANGERS]


def hard_block(code: str) -> Optional[str]:
    """Why this code is refused outright, or None. Not something the user can approve."""
    for pattern, why in _COMPILED_BLOCKS:
        if pattern.search(code or ""):
            return why
    return None


def code_dangers(code: str) -> List[str]:
    """Why code deserves a question even where the user has said not to be asked, in plain words (may be empty)."""
    seen: List[str] = []
    for pattern, why in _COMPILED_DANGERS:
        if pattern.search(code or "") and why not in seen:
            seen.append(why)
    return seen


def blocked_message(why: str) -> str:
    return (f"error: blocked. this would involve {why}, which isn't allowed here whatever the user says. "
            "do not retry it; do the task another way or say what you could not do.")


# --- "allow for this chat" ---

_allowed: Dict[str, Set[str]] = {}
_lock = threading.Lock()


def allow_for_chat(chat_id: str, kind: str) -> None:
    with _lock:
        _allowed.setdefault(chat_id, set()).add(kind)


def is_allowed(chat_id: str, kind: str) -> bool:
    with _lock:
        return kind in _allowed.get(chat_id, ())


def forget_chat(chat_id: str) -> None:
    """Take back what was allowed for a chat (it was deleted, or its permission mode changed)."""
    with _lock:
        _allowed.pop(chat_id, None)


# --- the decision ---

def how_it_proceeds(mode: str, chat_id: str, kind: str, dangers: List[str]) -> Optional[str]:
    """
    How a call that needs approval gets it without being asked: CHAT or BYPASS, or None when the user has to be
    asked. Anything dangerous always has to be asked about; in "safe" mode such calls never get this far.
    """
    if dangers:
        return None
    if mode == "bypass":
        return BYPASS
    if mode == "ask" and is_allowed(chat_id, kind):
        return CHAT
    return None


def why_asked(mode: str, chat_id: str, kind: str, dangers: List[str]) -> str:
    """The heading shown above a request that has something in it worth a close look (empty when nothing is)."""
    if not dangers:
        return ""
    why = "; ".join(dangers)
    if mode == "bypass":
        return f"ASKING EVEN THOUGH THIS CHAT IS IN BYPASS MODE: {why}"
    if is_allowed(chat_id, kind):
        return f"ASKING AGAIN, EVEN THOUGH YOU ALLOWED THIS FOR THE CHAT: {why}"
    # A save already says on its face that it replaces a file; code is where a flag adds something
    return f"WORTH A CLOSE LOOK: {why}" if kind == KIND_CODE else ""
