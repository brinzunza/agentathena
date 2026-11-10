"""
Chat branches: a branch is a chat forked from another one that carries on from
a snapshot of it, so the user can explore a side question without touching the
main chat, and then merge what turned out to matter back.

Creating a branch copies the parent's messages (up to the branch point), its
memory (context), settings, scratch workspace, research jobs and documents.
The documents are a copy rather than a shared folder, so a branch keeps
working if the parent is deleted and never sees documents added to the parent
later.

Merging goes to the chat the branch came from, in one of three ways: bring
over chosen exchanges, bring over a summary of the branch, or replace the
parent's conversation with the branch's.
"""

import copy
import os
import re
import shutil
from typing import List, Optional

import chat_manager
import context_manager

MODES = ("pick", "summary", "replace")

_JOB_ID = re.compile(r"[0-9a-f]{12}")


def _auto_name(parent_name: str, parent_id: str) -> str:
    prefix = f"{parent_name} · branch "
    taken = sum(
        1 for chat in chat_manager.list_chats()
        if chat.get("parent_id") == parent_id and chat["name"].startswith(prefix)
    )
    return f"{prefix}{taken + 1}"[: chat_manager.MAX_CHAT_NAME_CHARS]


def _copy_research(src_id: str, dst_id: str, messages: List[dict]) -> None:
    """Copy the research jobs that `messages` show cards for, so the cards still open."""
    src_dir = os.path.join(chat_manager._chat_dir(src_id), "research")
    dst_dir = os.path.join(chat_manager._chat_dir(dst_id), "research")
    for message in messages:
        job_id = message.get("research_id") if message.get("role") == "research" else None
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            continue
        src = os.path.join(src_dir, f"{job_id}.json")
        if os.path.isfile(src):
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copyfile(src, os.path.join(dst_dir, f"{job_id}.json"))


def create_branch(parent_id: str, name: Optional[str] = None, at_index: Optional[int] = None,
                  in_progress_upload_chat_ids: Optional[set] = None) -> dict:
    """
    Fork a chat. The branch starts with the parent's messages up to and
    including message `at_index` (all of them when None) and remembers what
    the parent remembered at that point.

    Returns:
        The new chat's metadata (as list_chats() shapes it)

    Raises:
        ValueError: if the chat doesn't exist, `at_index` is out of range, or
            the chat has an upload in progress
    """
    parent = chat_manager.get_meta(parent_id)
    messages = chat_manager.get_messages(parent_id)
    if at_index is None:
        keep = len(messages)
    elif isinstance(at_index, int) and not isinstance(at_index, bool) and 0 <= at_index < len(messages):
        keep = at_index + 1
    else:
        raise ValueError(f"There is no message {at_index} to branch from")

    name = (name or "").strip() or _auto_name(parent["name"], parent_id)
    if len(name) > chat_manager.MAX_CHAT_NAME_CHARS:
        raise ValueError(f"A chat name can be at most {chat_manager.MAX_CHAT_NAME_CHARS} characters")

    meta = chat_manager.create_chat(
        name, copy_from=parent_id, in_progress_upload_chat_ids=in_progress_upload_chat_ids,
        parent_id=parent_id, branch_point=keep,
    )
    branch_id = meta["id"]
    try:
        kept, removed = messages[:keep], messages[keep:]
        chat_manager.update_messages(branch_id, lambda _: copy.deepcopy(kept))

        context = chat_manager.get_context(parent_id)
        if removed:
            context = context_manager.trim_to_messages(context, kept, removed)
        chat_manager.update_context(branch_id, lambda _: copy.deepcopy(context))

        chat_manager.save_config(branch_id, chat_manager.get_config(parent_id))

        workspace = os.path.join(chat_manager._chat_dir(parent_id), "workspace")
        if os.path.isdir(workspace):
            shutil.copytree(workspace, os.path.join(chat_manager._chat_dir(branch_id), "workspace"),
                            ignore=shutil.ignore_patterns("_athena_*"))
        _copy_research(parent_id, branch_id, kept)
    except Exception:
        shutil.rmtree(chat_manager._chat_dir(branch_id), ignore_errors=True)
        raise
    return meta


def _exchange_at(messages: List[dict], index: int) -> List[dict]:
    """The assistant message at `index` and the user message it answers."""
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(messages):
        raise ValueError(f"There is no message {index} in this branch")
    answer = messages[index]
    if answer.get("role") != "assistant" or not isinstance(answer.get("content"), str) or not answer["content"].strip():
        raise ValueError(f"Message {index} isn't an answer that can be merged")
    for j in range(index - 1, -1, -1):
        if messages[j].get("role") == "user":
            return [messages[j], answer]
    raise ValueError(f"Message {index} has no question before it")


def merge_into_parent(branch_id: str, mode: str, message_indexes: Optional[List[int]] = None,
                      focus: str = "", delete_branch: bool = False) -> dict:
    """
    Merge a branch back into the chat it came from.

    pick     adds the exchanges at `message_indexes` (the answer's index; its
             question comes along) to the parent's messages and memory.
    summary  adds a summary of what the branch did since it was made.
    replace  makes the parent's messages and memory the branch's. The parent's
             documents and settings are left alone.

    Everything is worked out before anything is written, so a failed summary
    leaves the parent as it was. Documents added in the branch aren't moved.

    Returns:
        {"messages": the parent's messages now, "added": how many messages were
        added, "parent_id", "deleted": whether the branch was removed}

    Raises:
        ValueError: for an unknown mode, a chat that isn't a branch (or whose
            parent is gone), or nothing to merge
    """
    if mode not in MODES:
        raise ValueError(f"Unknown merge mode: {mode}")
    branch = chat_manager.get_meta(branch_id)
    parent_id = branch["parent_id"]
    if not parent_id:
        raise ValueError("This chat isn't a branch, so there is nothing to merge into")
    if not chat_manager.chat_exists(parent_id):
        raise ValueError("The chat this branch came from no longer exists")

    name = branch["name"]
    branch_messages = chat_manager.get_messages(branch_id)

    new_messages: List[dict]
    new_turns: List[dict] = []
    replacement_context: Optional[List[dict]] = None

    if mode == "pick":
        indexes = sorted(set(message_indexes or []))
        if not indexes:
            raise ValueError("Pick at least one answer to merge")
        by_question = {
            e["question"]: e for e in chat_manager.get_context(branch_id) if e["kind"] == "turn"
        }
        exchanges = [_exchange_at(branch_messages, i) for i in indexes]
        new_messages = [{"role": "system", "content": f"Merged {len(exchanges)} "
                         f"{'exchange' if len(exchanges) == 1 else 'exchanges'} from the branch “{name}”"}]
        for question, answer in exchanges:
            new_messages += [copy.deepcopy(question), {**copy.deepcopy(answer), "merged_from": branch_id}]
            looked_up = by_question.get(question["content"], {}).get("looked_up")
            new_turns.append({"question": question["content"], "answer": answer["content"], "looked_up": looked_up})
        copied_from = [m for pair in exchanges for m in pair]
    elif mode == "summary":
        # Clearing the branch's chat leaves it shorter than where it started: all of it is new then
        start = branch["branch_point"] or 0
        since = branch_messages[start:] if start <= len(branch_messages) else branch_messages
        text = context_manager.summarize_branch(name, since, focus)
        new_messages = [
            {"role": "system", "content": f"Merged a summary of the branch “{name}”"},
            {"role": "assistant", "content": text, "sources": None, "response_time": None, "merged_from": branch_id},
        ]
        new_turns.append({"question": f"Findings from the branch “{name}”", "answer": text})
        copied_from = []
    else:
        if not branch_messages:
            raise ValueError("The branch is empty, so replacing the chat with it would erase the chat")
        new_messages = copy.deepcopy(branch_messages) + [
            {"role": "system", "content": f"This chat now continues from the branch “{name}”"}
        ]
        replacement_context = copy.deepcopy(chat_manager.get_context(branch_id))
        copied_from = branch_messages

    _copy_research(branch_id, parent_id, copied_from)

    if mode == "replace":
        messages = chat_manager.update_messages(parent_id, lambda _: new_messages)
        chat_manager.update_context(parent_id, lambda _: replacement_context)
    else:
        messages = chat_manager.update_messages(parent_id, lambda current: current + new_messages)
        context_manager.add_turns(parent_id, new_turns)

    if delete_branch:
        chat_manager.delete_chat(branch_id)
    return {"messages": messages, "added": len(new_messages), "parent_id": parent_id, "deleted": delete_branch}
