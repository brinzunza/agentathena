"""
The server keeps the conversation, not the browser.

A question and its streamed reply used to be saved by the browser, which sent the whole message list
back after each reply. Close the tab mid-reply and the reply was lost; a second tab, or a merge, could
be overwritten by a stale list. Here the server appends the question when it starts answering, keeps
the reply up to date as events stream out, and settles it however the turn ends: finished, failed, or
the browser went away.

`TurnRecorder` applies the same events the browser applies (src/App.js) to a copy of the reply, so
what is saved is what the user watched. The browser still holds its own copy for display, and no longer
saves it for turns the server recorded.
"""

import threading
import time
from typing import Optional

import chat_manager

# Chunks arrive many times a second; writing the file for each would be pointless. Steps, results and
# the end of the turn are written straight away, and chunks at most this often.
CHUNK_SAVE_SECONDS = 1.5

INTERRUPTED_NOTICE = "this reply was stopped before it finished, so it was saved as far as it got."
RESTART_NOTICE = "the app was closed while this was being written, so it stopped here."


def settle_step(step: dict) -> dict:
    """A step cut off before its result arrived is stopped, and a prompt nobody answered has expired.
    The same rule as settleStep in frontend/src/steps.js."""
    step = dict(step)
    if step.get("status") == "running":
        step["status"] = "stopped"
    permission = step.get("permission")
    if permission and permission.get("state") == "pending":
        step["permission"] = {**permission, "state": "expired"}
    return step


def settle_reply(message: dict, notice: Optional[str] = None) -> dict:
    """A reply that is no longer being written: not streaming, no step left running."""
    settled = {key: value for key, value in message.items() if key != "streaming"}
    if "steps" in settled:
        settled["steps"] = [settle_step(step) for step in settled["steps"]]
    if notice:
        settled["notice"] = _join_notice(settled.get("notice"), notice)
    return settled


def _join_notice(current: Optional[str], new: str) -> str:
    if not current:
        return new
    return current if new in current else f"{current} {new}"


class TurnRecorder:
    """Saves one question and its reply as the events for it go by."""

    def __init__(self, chat_id: str, question: str, clock=time.monotonic):
        self.chat_id = chat_id
        self.question = question
        self.clock = clock
        self.reply = {"role": "assistant", "content": "", "sources": None, "response_time": None, "streaming": True}
        self.index: Optional[int] = None     # where the reply sits in the saved list
        self.closed = False
        self.completed = False           # the reply finished normally (not an error, not cut short)
        self._saved_at = None
        # The stream's thread applies events while the response's background task may settle the reply
        self._lock = threading.RLock()

    # --- the lifecycle ---

    def begin(self) -> int:
        """Append the question and an empty reply. Returns where the reply is."""
        def append(messages):
            return [*messages, {"role": "user", "content": self.question}, dict(self.reply)]

        saved = chat_manager.update_messages(self.chat_id, append)
        self.index = len(saved) - 1
        self._saved_at = self.clock()
        return self.index

    def apply(self, event: dict) -> None:
        """Fold one stream event into the reply and save when it is worth saving."""
        with self._lock:
            self._apply(event)

    def _apply(self, event: dict) -> None:
        if self.closed:
            return
        kind = event.get("type")
        reply = self.reply
        if kind == "chunk" and event.get("content"):
            reply["content"] += event["content"]
            if self.clock() - self._saved_at >= CHUNK_SAVE_SECONDS:
                self._write()
            return
        if kind == "reset":
            # What streamed so far was the agent thinking aloud before a tool, not the answer
            reply["content"] = ""
        elif kind == "step":
            reply.setdefault("steps", []).append({
                "id": event["id"], "tool": event["tool"], "args": event.get("args"),
                "thought": event.get("thought", ""), "status": "running",
            })
        elif kind == "step_result":
            for step in reply.get("steps", []):
                if step["id"] == event["id"]:
                    step["status"] = event["status"]
                    step["result"] = event["result"]
                    if event.get("approval"):
                        step["approval"] = event["approval"]
                    if step.get("permission", {}).get("state") == "pending":
                        step["permission"] = {**step["permission"], "state": "expired"}
        elif kind == "permission_request":
            for step in reply.get("steps", []):
                if step["id"] == event["step"]:
                    step["permission"] = {"id": event["id"], "code": event["code"],
                                          "kind": event.get("kind") or "code", "state": "pending",
                                          "can_remember": event.get("can_remember", True)}
        elif kind == "permission_decided":
            for step in reply.get("steps", []):
                if step["id"] == event["step"] and step.get("permission", {}).get("id") == event["id"]:
                    step["permission"] = {**step["permission"], "state": event["state"]}
        elif kind == "notice":
            reply["notice"] = _join_notice(reply.get("notice"), event["message"])
        elif kind == "sources" and event.get("content"):
            reply["sources"] = event["content"]
        elif kind == "complete":
            reply["response_time"] = event.get("response_time")
            self.completed = True
            self._finish(settle_reply(reply))
            return
        else:
            return          # chunk with no text, an error (handled by fail), anything unknown
        self._write()

    def fail(self, message: str, code: Optional[str] = None) -> None:
        """The turn ended in an error: the reply bubble is replaced by the error, as the browser shows it."""
        with self._lock:
            if not self.closed:
                self._finish({"role": "error", "content": message, "code": code})

    def interrupted(self) -> None:
        """The browser went away (or the user stopped the reply). Keep what there is. Safe to call again."""
        with self._lock:
            if self.closed:
                return
            notice = INTERRUPTED_NOTICE if self.reply["content"] or self.reply.get("steps") else None
            self._finish(settle_reply(self.reply, notice))

    # --- writing ---

    def _write(self) -> None:
        self._replace(self.reply)
        self._saved_at = self.clock()

    def _finish(self, final: dict) -> None:
        self.closed = True
        self._replace(final)

    def _replace(self, message: dict) -> None:
        """Overwrite our reply in the saved list, found by position and checked by what is there."""
        def put(messages):
            messages = list(messages)
            at = self._locate(messages)
            if at is None:
                return messages          # the chat was cleared or rewound meanwhile: nothing of ours is left to update
            messages[at] = dict(message)
            return messages

        chat_manager.update_messages(self.chat_id, put)

    def _locate(self, messages: list) -> Optional[int]:
        """Our reply is the still-streaming assistant message at the position it was appended at."""
        if self.index is not None and self.index < len(messages):
            candidate = messages[self.index]
            if candidate.get("role") == "assistant" and candidate.get("streaming"):
                return self.index
        for at in range(len(messages) - 1, -1, -1):
            if messages[at].get("role") == "assistant" and messages[at].get("streaming"):
                return at
        return None


def settle_interrupted_replies() -> int:
    """
    At startup: a reply still marked as streaming belongs to a run the last process never finished
    (it was closed or crashed). Settle each so the browser doesn't show a bubble that blinks forever.
    Returns how many were settled.
    """
    settled = 0
    for chat in chat_manager.list_chats():
        try:
            messages = chat_manager.get_messages(chat["id"])
        except (ValueError, OSError):
            continue
        if not any(m.get("streaming") for m in messages):
            continue

        def fix(current):
            return [settle_reply(m, RESTART_NOTICE) if m.get("streaming") else m for m in current]

        chat_manager.update_messages(chat["id"], fix)
        settled += sum(1 for m in messages if m.get("streaming"))
    return settled
