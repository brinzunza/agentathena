"""
A search index over every chat's name and messages, in a small SQLite file.

Searching used to open every chat's messages.json and scan it, which gets slower with every chat the user
keeps. This keeps the text in one SQLite full-text table (`chats/.search.db`) and finds candidates with an index
lookup. It uses the trigram tokenizer, so a search matches any run of three or more characters inside a word
(the way the old scan did), and anything shorter is checked against the candidates afterwards.

The index never has to be told about changes. Before each search it compares every chat's files with what it
last read (modification time and size) and re-reads only the chats that differ, so edits made any way at all
- the browser, a merge, a streamed reply, a person editing a file - show up. The files stay the source of
truth: the index can be deleted at any time and is rebuilt by the next search.

SQLite built without FTS5 (or without the trigram tokenizer) is reported by `available()`, and callers fall back
to scanning the files.
"""

import json
import os
import sqlite3
import threading
from typing import Callable, Dict, List, Optional, Tuple

INDEX_NAME = ".search.db"
MIN_INDEXED_TERM = 3                 # the trigram tokenizer can't look up anything shorter
CANDIDATE_LIMIT = 5000

_lock = threading.RLock()


class IndexUnavailable(Exception):
    """This SQLite can't do what the index needs."""


def available() -> bool:
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        connection.close()
        return True
    except sqlite3.Error:
        return False


def _connect(root: str) -> sqlite3.Connection:
    os.makedirs(root, exist_ok=True)
    connection = sqlite3.connect(os.path.join(root, INDEX_NAME), timeout=10)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS seen (chat_id TEXT PRIMARY KEY, stamp TEXT NOT NULL)")
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS text USING fts5("
                           "chat_id UNINDEXED, position UNINDEXED, role UNINDEXED, content, tokenize='trigram')")
    except sqlite3.Error as error:
        connection.close()
        raise IndexUnavailable(str(error)) from error
    return connection


def _stamp(*paths: str) -> str:
    """What the files looked like when they were read: changes when any of them is written."""
    parts = []
    for path in paths:
        try:
            info = os.stat(path)
            parts.append(f"{info.st_mtime_ns}:{info.st_size}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def _read(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _rows(chat_id: str, meta: dict, messages: list) -> List[Tuple[str, int, str, str]]:
    rows = [(chat_id, -1, "chat", meta.get("name", ""))]
    for position, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and message.get("role") in ("user", "assistant"):
            rows.append((chat_id, position, message["role"], content))
    return rows


def refresh(connection: sqlite3.Connection, root: str, chat_ids: List[str]) -> int:
    """Bring the index in line with the chat folders. Returns how many chats were re-read."""
    known: Dict[str, str] = dict(connection.execute("SELECT chat_id, stamp FROM seen"))
    reread = 0
    for chat_id in chat_ids:
        folder = os.path.join(root, chat_id)
        meta_path, messages_path = os.path.join(folder, "meta.json"), os.path.join(folder, "messages.json")
        stamp = _stamp(meta_path, messages_path)
        if known.get(chat_id) == stamp:
            continue
        meta = _read(meta_path, {})
        if not meta or meta.get("building"):
            connection.execute("DELETE FROM text WHERE chat_id = ?", (chat_id,))
            connection.execute("DELETE FROM seen WHERE chat_id = ?", (chat_id,))
            continue
        messages = _read(messages_path, [])
        connection.execute("DELETE FROM text WHERE chat_id = ?", (chat_id,))
        connection.executemany("INSERT INTO text (chat_id, position, role, content) VALUES (?, ?, ?, ?)",
                               _rows(chat_id, meta, messages if isinstance(messages, list) else []))
        connection.execute("INSERT OR REPLACE INTO seen (chat_id, stamp) VALUES (?, ?)", (chat_id, stamp))
        reread += 1
    gone = set(known) - set(chat_ids)
    for chat_id in gone:
        connection.execute("DELETE FROM text WHERE chat_id = ?", (chat_id,))
        connection.execute("DELETE FROM seen WHERE chat_id = ?", (chat_id,))
    connection.commit()
    return reread + len(gone)


def _quote(term: str) -> str:
    """One search term as an FTS5 phrase, so characters like * : - or the word NEAR mean nothing special."""
    return '"' + term.replace('"', '""') + '"'


def candidates(root: str, chat_ids: List[str], terms: List[str],
               matches: Callable[[str], bool]) -> List[Tuple[str, int, str, str]]:
    """
    (chat_id, position, role, content) for every row that holds all of `terms`. `position` is -1 for a chat's
    name. `matches` is the exact test (all terms, ignoring case); the index narrows, and it decides.
    """
    with _lock:
        connection = _connect(root)
        try:
            refresh(connection, root, chat_ids)
            long_terms = [term for term in terms if len(term) >= MIN_INDEXED_TERM]
            if long_terms:
                query = " AND ".join(_quote(term) for term in long_terms)
                found = connection.execute(
                    "SELECT chat_id, position, role, content FROM text WHERE text MATCH ? LIMIT ?",
                    (query, CANDIDATE_LIMIT)).fetchall()
            else:
                # Every term is shorter than a trigram: nothing to look up, so look at everything
                found = connection.execute("SELECT chat_id, position, role, content FROM text").fetchall()
            return [row for row in found if matches(row[3])]
        except sqlite3.Error as error:
            raise IndexUnavailable(str(error)) from error
        finally:
            connection.close()
