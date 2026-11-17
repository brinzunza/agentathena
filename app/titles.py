"""
Name a chat after what it is about.

A chat can be started without a name. It is shown as "new chat" until its first question has been answered, then
given a short title written from that exchange - by the model if it can, and by a trimmed copy of the question if
it can't - so the sidebar reads like a list of topics instead of a column of "new chat".

Titling happens after the reply is finished and off the request: it must never delay an answer or fail one. If the
user names the chat in the meantime, their name wins; a title is only ever written over the placeholder.
"""

import re
import threading
from typing import Callable, Optional

import chat_manager

PLACEHOLDER_NAME = "new chat"
MAX_WORDS = 6
MAX_CHARS = 48
ANSWER_CHARS = 400          # how much of the reply the model reads to name the chat
_FILLER = re.compile(r"^(?:please|can you|could you|would you|i want to|i would like to|help me|hey|hi|hello)[\s,]+", re.IGNORECASE)


def clean_title(raw: Optional[str]) -> str:
    """A model's reply made fit to be a title: one line, no quotes, no label, no trailing stop, short."""
    text = (raw or "").strip().splitlines()[0].strip() if (raw or "").strip() else ""
    text = re.sub(r"^(?:title|chat title|name)\s*[:\-]\s*", "", text, flags=re.IGNORECASE)
    text = text.strip(" \t\"'`*#_“”‘’.:;,-")
    words = text.split()
    if len(words) > MAX_WORDS:
        text = " ".join(words[:MAX_WORDS])
    return text[:MAX_CHARS].rstrip(" ,;:-")


def heuristic_title(question: str) -> str:
    """A title from the question alone, for when the model can't be asked: its first few words."""
    text = " ".join((question or "").split())
    for _ in range(3):
        trimmed = _FILLER.sub("", text)
        if trimmed == text:
            break
        text = trimmed
    text = text.split("?")[0].split(". ")[0]
    title = clean_title(text)
    return (title[:1].upper() + title[1:]) if title else ""


def generate_title(question: str, answer: str, writer: Optional[Callable] = None) -> str:
    """A title for an exchange: the model's if it gives a usable one, else one made from the question."""
    try:
        if writer is None:
            import llm_prompts          # not at import: it reads the provider settings
            writer = llm_prompts.get_title_writer()
        title = clean_title(writer.invoke({"question": question[:600], "answer": (answer or "")[:ANSWER_CHARS]}))
        if title and title.lower() != PLACEHOLDER_NAME:
            return title
    except Exception as error:
        print(f"WARNING: couldn't ask the model for a chat title ({error}); using the question")
    return heuristic_title(question)


def is_untitled(chat_id: str) -> bool:
    try:
        return bool(chat_manager.get_meta(chat_id).get("untitled"))
    except ValueError:
        return False


def title_chat(chat_id: str, question: str, answer: str, writer: Optional[Callable] = None) -> Optional[str]:
    """
    Give a still-untitled chat its title. Returns the title, or None if the chat has a name already (or is gone).
    The name is checked again after the model has answered, so one the user typed meanwhile is not overwritten.
    """
    if not is_untitled(chat_id):
        return None
    title = generate_title(question, answer, writer)
    if not title:
        return None
    return chat_manager.set_generated_title(chat_id, title)


def schedule(chat_id: str, question: str, answer: str) -> Optional[threading.Thread]:
    """Title the chat in the background, if it needs it. Never raises."""
    if not is_untitled(chat_id):
        return None

    def work():
        try:
            title_chat(chat_id, question, answer)
        except Exception as error:
            print(f"WARNING: titling chat {chat_id} failed: {error}")

    thread = threading.Thread(target=work, name="titler", daemon=True)
    thread.start()
    return thread
