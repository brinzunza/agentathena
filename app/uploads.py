"""
Chunked, resumable uploads.

A browser sends a PDF in pieces. The server keeps each in-progress upload as a
session on disk (inside the chat's folder), so progress is real - the number of
bytes the server has actually stored - and an upload that breaks halfway
(network drop, closed tab, backend restart) continues from where it stopped
instead of starting over.

    create_session   announce a file; get back an id and how much is already stored
    append_chunk     add the next piece, which must start exactly where the stored
                     part ends
    finalize         once everything has arrived, move the files into the chat's
                     document folder for processing
    abort            throw a session away

Every chunk is written at an explicit offset and the byte count is only
advanced after the data is safely on disk, so the stored count is always
something the client can trust when it asks "where did I get to?".
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from typing import Dict, List, Optional, Tuple

import chat_manager

CHUNK_BYTES = 1024 * 1024                  # the size the server suggests clients use
MAX_CHUNK_BYTES = 8 * 1024 * 1024          # the most it will accept in one request
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_OPEN_SESSIONS = 25                     # per chat, so abandoned uploads can't fill the disk
SESSION_TTL_SECONDS = 24 * 60 * 60         # an upload untouched this long is discarded

_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_META = "meta.json"
_DATA = "data.part"

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class UploadError(Exception):
    """A failure with the HTTP status the API should answer with, and any extra fields (e.g. `received`)."""

    def __init__(self, status: int, message: str, code: str = "error", **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.extra = extra

    def detail(self) -> dict:
        return {"message": self.message, "code": self.code, **self.extra}


def _lock_for(upload_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(upload_id, threading.Lock())


def _uploads_dir(chat_id: str) -> str:
    return os.path.join(chat_manager._chat_dir(chat_id), "uploads")


def _session_dir(chat_id: str, upload_id: str) -> str:
    # The id is only ever used as a folder name, so it must not be able to contain a path
    if not _ID_PATTERN.fullmatch(upload_id):
        raise UploadError(404, "upload not found", "not_found")
    return os.path.join(_uploads_dir(chat_id), upload_id)


def _read_meta(chat_id: str, upload_id: str) -> Optional[dict]:
    path = os.path.join(_session_dir(chat_id, upload_id), _META)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_meta(chat_id: str, upload_id: str, meta: dict) -> None:
    chat_manager._write_json_atomic(os.path.join(_session_dir(chat_id, upload_id), _META), meta)


def _public(meta: dict) -> dict:
    return {
        "upload_id": meta["id"],
        "filename": meta["filename"],
        "size": meta["size"],
        "received": meta["received"],
        "complete": meta["received"] >= meta["size"],
        "chunk_size": CHUNK_BYTES,
    }


def sanitize_filename(name: str) -> str:
    """
    A safe name for a stored PDF: no folders, no control characters, and always
    a lowercase .pdf extension.

    Raises:
        UploadError: 400 if it isn't a PDF name
    """
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f]", "", base).strip()
    if not base.lower().endswith(".pdf"):
        raise UploadError(400, f"only pdf files allowed: {base or name!r}", "not_pdf")
    stem = base[:-4].strip().lstrip(".")
    if not stem:
        raise UploadError(400, "that file has no name", "not_pdf")
    return stem[:150] + ".pdf"


def create_session(chat_id: str, filename: str, size: int, last_modified: int = 0) -> dict:
    """
    Start an upload, or pick up one that was interrupted.

    A file with the same name, size and modified time as an unfinished session
    in this chat is the same upload: the existing session is returned, with
    `received` telling the client where to continue from. That is what makes a
    resume work even after a page reload.

    Raises:
        UploadError: 400 bad name or size, 413 too big, 429 too many open uploads
    """
    name = sanitize_filename(filename)
    if size <= 0:
        raise UploadError(400, "that file is empty", "empty")
    if size > MAX_FILE_BYTES:
        raise UploadError(413, f"files can be at most {MAX_FILE_BYTES // (1024 * 1024)} MB", "too_big")

    cleanup_stale(chat_id)
    sessions = list_sessions(chat_id)
    for meta in sessions:
        if (meta["filename"], meta["size"], meta.get("last_modified", 0)) == (name, size, last_modified):
            return {**_public(meta), "resumed": meta["received"] > 0}
    if len(sessions) >= MAX_OPEN_SESSIONS:
        raise UploadError(429, "too many unfinished uploads in this chat. finish or cancel some first", "too_many")

    upload_id = uuid.uuid4().hex
    os.makedirs(_session_dir(chat_id, upload_id))
    now = time.time()
    meta = {"id": upload_id, "filename": name, "size": size, "last_modified": last_modified,
            "received": 0, "created_at": now, "updated_at": now}
    open(os.path.join(_session_dir(chat_id, upload_id), _DATA), "wb").close()
    _write_meta(chat_id, upload_id, meta)
    return {**_public(meta), "resumed": False}


def get_session(chat_id: str, upload_id: str) -> Optional[dict]:
    meta = _read_meta(chat_id, upload_id)
    return _public(meta) if meta else None


def list_sessions(chat_id: str) -> List[dict]:
    """Every unfinished upload in a chat (stored metadata, oldest first)."""
    directory = _uploads_dir(chat_id)
    if not os.path.isdir(directory):
        return []
    sessions = []
    for name in os.listdir(directory):
        if _ID_PATTERN.fullmatch(name):
            meta = _read_meta(chat_id, name)
            if meta:
                sessions.append(meta)
    return sorted(sessions, key=lambda meta: meta["created_at"])


def append_chunk(chat_id: str, upload_id: str, offset: int, data: bytes, checksum: Optional[str] = None) -> dict:
    """
    Store the next piece of an upload.

    The piece must begin exactly where the stored data ends; otherwise nothing
    is written and the error carries the true `received` count, so the client
    can resynchronise (for instance when the reply to the previous request was
    lost, but the data had arrived).

    Raises:
        UploadError: 404 no such upload, 400 empty or overlong chunk, 409 wrong
            offset, 413 chunk too big, 422 checksum mismatch
    """
    if not data:
        raise UploadError(400, "empty chunk", "empty")
    if len(data) > MAX_CHUNK_BYTES:
        raise UploadError(413, f"chunks can be at most {MAX_CHUNK_BYTES // (1024 * 1024)} MB", "chunk_too_big")

    with _lock_for(upload_id):
        meta = _read_meta(chat_id, upload_id)
        if not meta:
            raise UploadError(404, "upload not found", "not_found")
        if offset != meta["received"]:
            raise UploadError(409, "that chunk doesn't start where the stored data ends", "offset_mismatch",
                              received=meta["received"])
        if offset + len(data) > meta["size"]:
            raise UploadError(400, "that chunk runs past the end of the file", "past_end")
        if checksum and hashlib.sha256(data).hexdigest() != checksum.strip().lower():
            raise UploadError(422, "the chunk was damaged in transit", "checksum")

        path = os.path.join(_session_dir(chat_id, upload_id), _DATA)
        with open(path, "r+b" if os.path.exists(path) else "wb") as f:
            f.seek(offset)
            f.truncate(offset)    # drop anything past the stored count, e.g. a write that was cut off
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        meta["received"] = offset + len(data)
        meta["updated_at"] = time.time()
        _write_meta(chat_id, upload_id, meta)
        return _public(meta)


def finalize(chat_id: str, upload_ids: List[str], dest_dir: str) -> List[Tuple[str, str]]:
    """
    Move finished uploads into `dest_dir` and discard their sessions.

    Everything is checked before anything is moved: every upload must exist,
    be complete, and start with the PDF signature. One that fails the signature
    check is discarded, since resuming it would only reproduce the same bytes.

    Returns:
        [(final path, filename)] in the order given

    Raises:
        UploadError: 404 unknown upload, 409 not finished yet, 400 not a PDF
    """
    ordered = sorted(set(upload_ids))
    locks = [_lock_for(upload_id) for upload_id in ordered]
    for lock in locks:
        lock.acquire()
    try:
        metas = {}
        for upload_id in upload_ids:
            meta = _read_meta(chat_id, upload_id)
            if not meta:
                raise UploadError(404, "an upload in this batch was not found (it may have expired)", "not_found")
            if meta["received"] < meta["size"]:
                raise UploadError(409, f"{meta['filename']} hasn't finished uploading", "incomplete",
                                  upload_id=upload_id, received=meta["received"])
            with open(os.path.join(_session_dir(chat_id, upload_id), _DATA), "rb") as f:
                signature = f.read(5)
            if signature != b"%PDF-":
                shutil.rmtree(_session_dir(chat_id, upload_id), ignore_errors=True)
                raise UploadError(400, f"{meta['filename']} isn't a valid PDF", "not_pdf")
            metas[upload_id] = meta

        os.makedirs(dest_dir, exist_ok=True)
        taken, results = set(), []
        for upload_id in upload_ids:
            name = _unique_name(metas[upload_id]["filename"], taken)
            taken.add(name)
            final = os.path.join(dest_dir, name)
            os.replace(os.path.join(_session_dir(chat_id, upload_id), _DATA), final)
            shutil.rmtree(_session_dir(chat_id, upload_id), ignore_errors=True)
            results.append((final, name))
        return results
    finally:
        for lock in locks:
            lock.release()


def _unique_name(name: str, taken: set) -> str:
    """Two files with the same name in one batch: 'a.pdf', 'a (2).pdf'."""
    if name not in taken:
        return name
    stem = name[:-4]
    number = 2
    while f"{stem} ({number}).pdf" in taken:
        number += 1
    return f"{stem} ({number}).pdf"


def abort(chat_id: str, upload_id: str) -> None:
    """Discard a session and whatever it had stored. Safe to call on one that is already gone."""
    with _lock_for(upload_id):
        shutil.rmtree(_session_dir(chat_id, upload_id), ignore_errors=True)


def cleanup_stale(chat_id: Optional[str] = None, now: Optional[float] = None) -> int:
    """
    Discard uploads nobody has touched for SESSION_TTL_SECONDS (one chat, or all
    of them), including folders whose metadata is missing or unreadable.

    Returns:
        how many sessions were removed
    """
    now = time.time() if now is None else now
    chat_ids = [chat_id] if chat_id else [
        name for name in (os.listdir(chat_manager.CHATS_ROOT) if os.path.isdir(chat_manager.CHATS_ROOT) else [])
        if os.path.isdir(_uploads_dir(name))
    ]
    removed = 0
    for current in chat_ids:
        directory = _uploads_dir(current)
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not _ID_PATTERN.fullmatch(name):
                continue
            meta = _read_meta(current, name)
            updated = meta["updated_at"] if meta else os.path.getmtime(os.path.join(directory, name))
            if now - updated > SESSION_TTL_SECONDS:
                with _lock_for(name):
                    shutil.rmtree(os.path.join(directory, name), ignore_errors=True)
                removed += 1
    return removed
