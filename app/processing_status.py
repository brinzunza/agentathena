"""
The life of a document-processing job, in one place.

A batch of uploaded PDFs is converted, indexed and cleaned up on a background
thread while the browser polls its progress. That progress lives in a dict in
the API process. These names, and what counts as finished, are defined here so
nothing compares against a bare string: adding a state means adding it here.

    uploading -> converting -> indexing -> cleanup -> completed
                       \\__________\\___________\\____-> error

Finished entries are dropped after a while, so the dict doesn't grow for as long
as the server runs. A job that is lost with the process (a restart) simply isn't
there any more; the browser treats a missing job as interrupted, not as done.
"""

import time
from typing import Dict, Optional

UPLOADING = "uploading"
CONVERTING = "converting"
INDEXING = "indexing"
CLEANUP = "cleanup"
COMPLETED = "completed"
ERROR = "error"

# In the order a job passes through them (error can follow any stage)
ALL = (UPLOADING, CONVERTING, INDEXING, CLEANUP, COMPLETED, ERROR)
TERMINAL = frozenset({COMPLETED, ERROR})

FINISHED_TTL_SECONDS = 10 * 60

# What each stage is called in a failure message
STAGE_LABELS = {
    UPLOADING: "receiving the files",
    CONVERTING: "converting the PDFs",
    INDEXING: "indexing the documents",
    CLEANUP: "cleaning up",
}


def is_active(entry: dict) -> bool:
    """Whether a job is still working, i.e. its chat can't be copied or branched yet."""
    return entry.get("status") not in TERMINAL


def finish(entry: dict, now: Optional[float] = None) -> None:
    """Stamp a job that has just reached a terminal state, so it can be dropped later."""
    entry["finished_at"] = time.time() if now is None else now


def failure_message(stage: str, error: Exception) -> str:
    """'processing failed while indexing the documents: ...' - which stage, then the reason."""
    return f"processing failed while {STAGE_LABELS.get(stage, 'processing')}: {error}"


def prune(progress: Dict[str, dict], now: Optional[float] = None, ttl: float = FINISHED_TTL_SECONDS) -> int:
    """Drop jobs that finished more than `ttl` seconds ago. Returns how many were dropped."""
    now = time.time() if now is None else now
    stale = [key for key, entry in progress.items()
             if not is_active(entry) and now - entry.get("finished_at", now) > ttl]
    for key in stale:
        del progress[key]
    return len(stale)
