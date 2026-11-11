import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from typing import List, Optional

from document_processor import list_documents

CHATS_ROOT = "chats"

# Serializes read-modify-write cycles on messages.json (see update_messages).
_messages_lock = threading.RLock()
_meta_lock = threading.Lock()


def _chat_dir(chat_id: str) -> str:
    return os.path.join(CHATS_ROOT, chat_id)


def _meta_path(chat_id: str) -> str:
    return os.path.join(_chat_dir(chat_id), "meta.json")


def _messages_path(chat_id: str) -> str:
    return os.path.join(_chat_dir(chat_id), "messages.json")


def _context_path(chat_id: str) -> str:
    return os.path.join(_chat_dir(chat_id), "context.json")


def _config_path(chat_id: str) -> str:
    return os.path.join(_chat_dir(chat_id), "config.json")


def get_workspace_path(chat_id: str) -> str:
    """Absolute path of the chat's scratch folder for agent-run code (created on demand)."""
    path = os.path.abspath(os.path.join(_chat_dir(chat_id), "workspace"))
    os.makedirs(path, exist_ok=True)
    return path


def chat_exists(chat_id: str) -> bool:
    return os.path.exists(_meta_path(chat_id))


def get_messages(chat_id: str) -> List[dict]:
    """
    Load a chat's persisted conversation history.

    Args:
        chat_id: The chat's id

    Returns:
        List of message dicts, oldest first. Empty if none saved yet.

    Raises:
        ValueError: if the chat doesn't exist
    """
    if not chat_exists(chat_id):
        raise ValueError(f"Chat not found: {chat_id}")
    path = _messages_path(chat_id)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_messages(chat_id: str, messages: List[dict]) -> None:
    """
    Persist a chat's full conversation history, overwriting whatever was
    saved before. The frontend calls this with its current in-memory
    message list at each settled point (a message finishing, not mid-stream)
    rather than one message at a time, so this is a full replace, not an
    append.

    Args:
        chat_id: The chat's id
        messages: The complete message list to save

    Raises:
        ValueError: if the chat doesn't exist
    """
    update_messages(chat_id, lambda _: messages)


def update_messages(chat_id: str, updater) -> List[dict]:
    """
    Atomically apply updater(messages) -> new_messages to a chat's history.

    Merging a branch appends to its parent's messages on the server, which
    must not interleave with the client saving the same list.

    Raises:
        ValueError: if the chat doesn't exist
    """
    with _messages_lock:
        messages = get_messages(chat_id)
        new_messages = updater(messages)
        _write_json_atomic(_messages_path(chat_id), new_messages)
        return new_messages


def get_chat_paths(chat_id: str):
    """
    Resolve a chat's chroma_db/files paths.

    Args:
        chat_id: The chat's id

    Returns:
        (chroma_db_path, files_path) tuple

    Raises:
        ValueError: if the chat doesn't exist
    """
    if not chat_exists(chat_id):
        raise ValueError(f"Chat not found: {chat_id}")
    chat_dir = _chat_dir(chat_id)
    return os.path.join(chat_dir, "chroma_db"), os.path.join(chat_dir, "files")


def list_chats() -> List[dict]:
    """
    List all chats with their document counts, oldest first.

    Returns:
        List of {id, name, created_at, document_count, parent_id, branch_point} dicts
    """
    if not os.path.exists(CHATS_ROOT):
        return []

    chats = []
    for entry in os.listdir(CHATS_ROOT):
        meta_path = _meta_path(entry)
        if not os.path.exists(meta_path):
            continue
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("building"):
            continue          # a branch still being copied: not a chat yet
        chroma_db_path, files_path = get_chat_paths(entry)
        meta["document_count"] = len(list_documents(chroma_db_path, files_path))
        meta.setdefault("parent_id", None)
        meta.setdefault("branch_point", None)
        chats.append(meta)

    chats.sort(key=lambda c: c["created_at"])
    return chats


SEARCH_SNIPPET_CHARS = 140


def _snippet(text: str, terms: List[str]) -> str:
    """A one-line excerpt of `text` around the first place a search term appears."""
    flat = " ".join(text.split())
    lowered = flat.lower()
    at = min((lowered.find(term) for term in terms if term in lowered), default=0)
    start = max(0, at - SEARCH_SNIPPET_CHARS // 3)
    end = min(len(flat), start + SEARCH_SNIPPET_CHARS)
    return ("…" if start > 0 else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


def search_chats(query: str, limit: int = 50, per_chat: int = 5) -> List[dict]:
    """
    Find chats by name and messages by text.

    Every word of the query must appear (in any order, ignoring case). Chats
    are searched newest first and so are the messages inside each one, so what
    comes back first is what was said most recently.

    Returns:
        [{chat_id, chat_name, index, role, snippet}] where `index` is the
        message's position in the chat, or None when the chat's name matched.
    """
    terms = query.lower().split()
    if not terms or not os.path.exists(CHATS_ROOT):
        return []

    chats = []
    for entry in os.listdir(CHATS_ROOT):
        try:
            with open(_meta_path(entry), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if not meta.get("building"):
            chats.append(meta)
    chats.sort(key=lambda chat: chat["created_at"], reverse=True)

    results = []
    for chat in chats:
        found = 0
        if all(term in chat["name"].lower() for term in terms):
            results.append({"chat_id": chat["id"], "chat_name": chat["name"], "index": None,
                            "role": "chat", "snippet": chat["name"]})
        try:
            messages = get_messages(chat["id"])
        except (ValueError, OSError):
            continue
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            content = message.get("content")
            if message.get("role") not in ("user", "assistant") or not isinstance(content, str):
                continue
            lowered = content.lower()
            if all(term in lowered for term in terms):
                results.append({"chat_id": chat["id"], "chat_name": chat["name"], "index": index,
                                "role": message["role"], "snippet": _snippet(content, terms)})
                found += 1
                if found >= per_chat:
                    break
        if len(results) >= limit:
            break
    return results[:limit]


def create_chat(name: str, copy_from: Optional[str] = None, in_progress_upload_chat_ids: Optional[set] = None,
                parent_id: Optional[str] = None, branch_point: Optional[int] = None, building: bool = False) -> dict:
    """
    Create a new chat, either empty or as a copy of another chat's documents.

    Copying is a plain filesystem copy of the source chat's chroma_db/ and
    files/ directories, not a re-embed through the Chroma API - so cloning
    even a large collection is near-instant and re-uses the already-computed
    summaries and embeddings at no cost.

    Args:
        name: Display name for the new chat
        copy_from: Optional id of an existing chat to clone documents from
        in_progress_upload_chat_ids: chat ids that currently have an upload in
            progress (from backend/main.py's upload_progress) - copying a
            chat mid-write risks cloning a half-written collection
        parent_id, branch_point: set when the chat is a branch of another
            (see branches.create_branch): the chat it came from, and how many
            of that chat's messages it started with
        building: mark the chat as still being put together. It is left out of
            list_chats() until finish_building() clears the mark, and
            discard_unfinished_chats() removes one a crash left behind

    Returns:
        The new chat's metadata dict (matches list_chats()'s shape)

    Raises:
        ValueError: if copy_from is set but invalid, or has an upload in progress
    """
    if copy_from is not None:
        if not chat_exists(copy_from):
            raise ValueError(f"Chat not found: {copy_from}")
        if in_progress_upload_chat_ids and copy_from in in_progress_upload_chat_ids:
            raise ValueError("Source chat has an upload in progress - try again once it finishes")

    chat_id = str(uuid.uuid4())
    chat_dir = _chat_dir(chat_id)
    os.makedirs(chat_dir, exist_ok=True)

    if copy_from is not None:
        src_chroma_db, src_files = get_chat_paths(copy_from)
        if os.path.exists(src_chroma_db):
            shutil.copytree(src_chroma_db, os.path.join(chat_dir, "chroma_db"))
        if os.path.exists(src_files):
            shutil.copytree(src_files, os.path.join(chat_dir, "files"))

    os.makedirs(os.path.join(chat_dir, "files"), exist_ok=True)

    meta = {
        "id": chat_id,
        "name": name,
        "created_at": datetime.now().isoformat(),
    }
    if parent_id is not None:
        meta["parent_id"] = parent_id
        meta["branch_point"] = branch_point
    if building:
        meta["building"] = True
    with open(_meta_path(chat_id), "w", encoding="utf-8") as f:
        json.dump(meta, f)

    chroma_db_path, files_path = get_chat_paths(chat_id)
    meta["document_count"] = len(list_documents(chroma_db_path, files_path))
    meta.setdefault("parent_id", None)
    meta.setdefault("branch_point", None)
    return meta


def get_meta(chat_id: str) -> dict:
    """
    A chat's stored metadata: {id, name, created_at, parent_id, branch_point}.

    Raises:
        ValueError: if the chat doesn't exist
    """
    if not chat_exists(chat_id):
        raise ValueError(f"Chat not found: {chat_id}")
    with open(_meta_path(chat_id), "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta.setdefault("parent_id", None)
    meta.setdefault("branch_point", None)
    return meta


def finish_building(chat_id: str) -> dict:
    """Clear a chat's 'still being built' mark, so it shows up in the list. Returns its metadata."""
    with _meta_lock:
        meta = get_meta(chat_id)
        if meta.pop("building", None):
            _write_json_atomic(_meta_path(chat_id), meta)
        return meta


def discard_unfinished_chats() -> int:
    """
    Delete chats still marked as being built. Meant for startup, when nothing can be mid-copy:
    one that is left was cut short by a crash or a killed process. Returns how many were removed.
    """
    if not os.path.exists(CHATS_ROOT):
        return 0
    removed = 0
    for entry in os.listdir(CHATS_ROOT):
        if not os.path.exists(_meta_path(entry)):
            continue
        try:
            with open(_meta_path(entry), "r", encoding="utf-8") as f:
                unfinished = json.load(f).get("building")
        except (OSError, ValueError):
            continue
        if unfinished:
            shutil.rmtree(_chat_dir(entry), ignore_errors=True)
            removed += 1
    return removed


def update_meta(chat_id: str, **fields) -> dict:
    """Change fields of a chat's metadata (not its id). Returns the new metadata."""
    with _meta_lock:
        meta = get_meta(chat_id)
        meta.update({key: value for key, value in fields.items() if key != "id"})
        _write_json_atomic(_meta_path(chat_id), meta)
        return meta


def rename_chat(chat_id: str, name: str) -> dict:
    """
    Give a chat a new display name.

    Raises:
        ValueError: if the chat doesn't exist or the name is empty or too long
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A chat name can't be empty")
    if len(name) > MAX_CHAT_NAME_CHARS:
        raise ValueError(f"A chat name can be at most {MAX_CHAT_NAME_CHARS} characters")
    return update_meta(chat_id, name=name)


MAX_CHAT_NAME_CHARS = 80


def get_children(chat_id: str) -> List[str]:
    """Ids of the chats branched directly from this one."""
    if not os.path.exists(CHATS_ROOT):
        return []
    children = []
    for entry in os.listdir(CHATS_ROOT):
        if entry != chat_id and os.path.exists(_meta_path(entry)) and get_meta(entry).get("parent_id") == chat_id:
            children.append(entry)
    return children


def delete_chat(chat_id: str) -> bool:
    """
    Delete a chat entirely - its documents, files, and metadata.

    Branches of the chat survive: each holds its own copy of the documents, and
    moves up to be a branch of the deleted chat's parent (or a top-level chat).

    Args:
        chat_id: The chat's id

    Returns:
        True if the chat was found and deleted, False if it didn't exist
    """
    if not chat_exists(chat_id):
        return False
    grandparent = get_meta(chat_id).get("parent_id")
    children = get_children(chat_id)
    shutil.rmtree(_chat_dir(chat_id))
    for child in children:
        update_meta(child, parent_id=grandparent)
    return True


# --- Conversation context & per-chat configuration ---
#
# The context is what the model is shown of the conversation so far, kept
# separate from messages.json (what the UI displays) so the user can prune,
# compact, or narrow it without touching the visible chat history.
#
# Each entry is one of:
#   {"id", "kind": "turn",    "question", "answer", "created_at"}
#   {"id", "kind": "summary", "text",               "created_at"}
# Compacting or narrowing the context folds entries into a single summary
# entry, so there is at most one, and it's always first.

DEFAULT_CONFIG = {
    "context_enabled": True,
    "max_context_entries": 4,
    # What is sent is capped by both: the most recent exchanges, up to this
    # many tokens (estimated), whichever runs out first.
    "max_context_tokens": 3000,
    # Fold exchanges that fall out of the window into the summary instead of
    # dropping them.
    "auto_compact": False,
    # Agent: lets the model decide which tools to use per message. When off
    # (or when the model can't call tools) the classic retrieve -> generate
    # pipeline answers instead.
    "agent_enabled": True,
    # safe: the agent can't run code. ask: each run needs the user's OK.
    # bypass: code runs without asking.
    "permission_mode": "ask",
    "max_agent_steps": 8,
    "web_search_enabled": True,
}

PERMISSION_MODES = ("safe", "ask", "bypass")

# Serializes read-modify-write cycles on context.json: a streaming response
# appending its turn can land at the same moment as a delete or compact.
_context_lock = threading.Lock()


def _write_json_atomic(path: str, data) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def get_context(chat_id: str) -> List[dict]:
    """
    Load a chat's context entries, oldest first.

    Raises:
        ValueError: if the chat doesn't exist
    """
    if not chat_exists(chat_id):
        raise ValueError(f"Chat not found: {chat_id}")
    path = _context_path(chat_id)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def update_context(chat_id: str, updater) -> List[dict]:
    """
    Atomically apply updater(entries) -> new_entries to a chat's context.

    Args:
        chat_id: The chat's id
        updater: Callable taking the current entry list and returning the
            list to save. Runs under a lock, so keep it quick - do slow
            work (like LLM calls) before calling this, and have the updater
            only splice the result into whatever is current.

    Returns:
        The saved entry list

    Raises:
        ValueError: if the chat doesn't exist
    """
    with _context_lock:
        entries = get_context(chat_id)
        new_entries = updater(entries)
        _write_json_atomic(_context_path(chat_id), new_entries)
        return new_entries


def get_config(chat_id: str) -> dict:
    """
    Load a chat's configuration, with defaults filled in for anything unset.

    Raises:
        ValueError: if the chat doesn't exist
    """
    if not chat_exists(chat_id):
        raise ValueError(f"Chat not found: {chat_id}")
    config = dict(DEFAULT_CONFIG)
    path = _config_path(chat_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config.update(json.load(f))
    return config


def save_config(chat_id: str, config: dict) -> dict:
    """
    Persist a chat's configuration. Only known keys are kept.

    Raises:
        ValueError: if the chat doesn't exist
    """
    if not chat_exists(chat_id):
        raise ValueError(f"Chat not found: {chat_id}")
    merged = {key: config.get(key, default) for key, default in DEFAULT_CONFIG.items()}
    _write_json_atomic(_config_path(chat_id), merged)
    return merged
