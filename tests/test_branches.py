"""Chat branches: forking a chat, keeping branches independent of it, and merging back."""

import json
import os

import chat_manager
import branches
import context_manager
import main
import pytest
from fastapi.testclient import TestClient

from test_context_manager import fake_compactor


def user(text):
    return {"role": "user", "content": text}


def athena(text, **extra):
    return {"role": "assistant", "content": text, "sources": [], "response_time": 1.2, **extra}


def exchange(chat_id, question, answer, **extra):
    """An exchange as the app leaves it: two messages, and an entry in the memory."""
    chat_manager.update_messages(chat_id, lambda m: m + [user(question), athena(answer, **extra)])
    context_manager.record_turn(chat_id, question, answer, extra.get("looked_up"))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def parent(chat_id):
    exchange(chat_id, "my name is John", "Hi John.")
    exchange(chat_id, "what is the capital of France?", "Paris.")
    exchange(chat_id, "and of Spain?", "Madrid.")
    chat_manager.save_config(chat_id, {**chat_manager.get_config(chat_id), "max_agent_steps": 3})
    chat_dir = chat_manager._chat_dir(chat_id)
    write(os.path.join(chat_dir, "files", "notes.md"), "# notes")
    write(os.path.join(chat_dir, "chroma_db", "chroma.sqlite3"), "db")
    write(os.path.join(chat_dir, "workspace", "data.csv"), "a,b")
    write(os.path.join(chat_dir, "workspace", "_athena_run.py"), "print(1)")
    return chat_id


@pytest.fixture
def client():
    return TestClient(main.app)


def files_of(chat_id):
    return sorted(os.listdir(os.path.join(chat_manager._chat_dir(chat_id), "files")))


# --- making a branch ---

def test_a_branch_starts_from_a_copy_of_everything_in_the_parent(parent):
    meta = branches.create_branch(parent, "side question")
    b = meta["id"]

    assert (meta["name"], meta["parent_id"], meta["branch_point"]) == ("side question", parent, 6)
    assert chat_manager.get_messages(b) == chat_manager.get_messages(parent)
    assert chat_manager.get_context(b) == chat_manager.get_context(parent)
    assert chat_manager.get_config(b)["max_agent_steps"] == 3
    assert files_of(b) == ["notes.md"]
    assert os.path.isfile(os.path.join(chat_manager._chat_dir(b), "chroma_db", "chroma.sqlite3"))
    workspace = os.path.join(chat_manager._chat_dir(b), "workspace")
    assert os.listdir(workspace) == ["data.csv"]          # the run's scratch file isn't carried over


def test_a_branch_is_listed_with_its_parent(parent):
    b = branches.create_branch(parent)["id"]
    listed = {c["id"]: c for c in chat_manager.list_chats()}
    assert listed[b]["parent_id"] == parent and listed[b]["branch_point"] == 6
    assert listed[parent]["parent_id"] is None


def test_branches_are_named_after_their_parent_when_no_name_is_given(parent):
    assert branches.create_branch(parent)["name"] == "test chat · branch 1"
    assert branches.create_branch(parent)["name"] == "test chat · branch 2"


def test_branching_does_not_change_the_parent(parent):
    before = (chat_manager.get_messages(parent), chat_manager.get_context(parent), files_of(parent))
    branches.create_branch(parent)
    assert (chat_manager.get_messages(parent), chat_manager.get_context(parent), files_of(parent)) == before


# --- independence ---

def test_changes_in_either_chat_never_reach_the_other(parent):
    b = branches.create_branch(parent)["id"]

    write(os.path.join(chat_manager._chat_dir(parent), "files", "later.md"), "added to parent after")
    exchange(parent, "parent only", "yes")
    exchange(b, "branch only", "yes")

    assert files_of(b) == ["notes.md"]
    assert files_of(parent) == ["later.md", "notes.md"]
    assert [m["content"] for m in chat_manager.get_messages(b)][-2:] == ["branch only", "yes"]
    assert "branch only" not in [e["question"] for e in chat_manager.get_context(parent)]
    assert "parent only" not in [e["question"] for e in chat_manager.get_context(b)]


def test_a_branch_survives_its_parent_being_deleted(parent):
    b = branches.create_branch(parent)["id"]
    assert chat_manager.delete_chat(parent)

    assert chat_manager.get_meta(b)["parent_id"] is None            # now a top-level chat
    assert files_of(b) == ["notes.md"]                              # still has its own documents
    assert os.path.isfile(os.path.join(chat_manager._chat_dir(b), "chroma_db", "chroma.sqlite3"))
    assert len(chat_manager.get_messages(b)) == 6


def test_deleting_a_chat_moves_its_branches_up_to_its_parent(parent):
    middle = branches.create_branch(parent, "middle")["id"]
    leaf = branches.create_branch(middle, "leaf")["id"]
    assert chat_manager.get_meta(leaf)["parent_id"] == middle

    chat_manager.delete_chat(middle)

    assert chat_manager.get_meta(leaf)["parent_id"] == parent


def test_branches_can_be_branched_again(parent):
    first = branches.create_branch(parent, "one")["id"]
    second = branches.create_branch(first, "two")["id"]
    third = branches.create_branch(second, "three")["id"]
    assert [chat_manager.get_meta(c)["parent_id"] for c in (first, second, third)] == [parent, first, second]


# --- branching from the middle of a chat ---

def test_branching_at_a_message_keeps_only_the_messages_up_to_it(parent):
    meta = branches.create_branch(parent, at_index=3)             # up to "Paris."
    b = meta["id"]
    assert meta["branch_point"] == 4
    assert [m["content"] for m in chat_manager.get_messages(b)] == \
        ["my name is John", "Hi John.", "what is the capital of France?", "Paris."]
    assert [e["question"] for e in chat_manager.get_context(b)] == \
        ["my name is John", "what is the capital of France?"]


def test_branching_partway_keeps_the_summary_and_remember_notes(parent):
    chat_manager.update_context(parent, lambda e: [context_manager._summary_entry("older stuff")] + e)
    context_manager.record_turn(parent, "Remember: I prefer metric", "Noted.")   # not tied to a message
    b = branches.create_branch(parent, at_index=1)["id"]

    questions = [e.get("question") or "SUMMARY" for e in chat_manager.get_context(b)]
    assert questions == ["SUMMARY", "my name is John", "Remember: I prefer metric"]


@pytest.mark.parametrize("index", [-1, 6, 99])
def test_branching_at_a_message_that_does_not_exist_is_refused_and_leaves_nothing_behind(parent, index):
    with pytest.raises(ValueError):
        branches.create_branch(parent, at_index=index)
    assert [c["id"] for c in chat_manager.list_chats()] == [parent]


def test_branching_an_unknown_chat_is_refused():
    with pytest.raises(ValueError, match="not found"):
        branches.create_branch("nope")


def test_branching_is_refused_while_the_chat_is_uploading_and_leaves_nothing_behind(parent):
    with pytest.raises(ValueError, match="upload"):
        branches.create_branch(parent, in_progress_upload_chat_ids={parent})
    assert [c["id"] for c in chat_manager.list_chats()] == [parent]


def test_a_failed_branch_leaves_no_half_built_chat(parent, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(branches, "_copy_research", boom)
    with pytest.raises(RuntimeError):
        branches.create_branch(parent)
    assert [c["id"] for c in chat_manager.list_chats()] == [parent]


def test_research_reports_in_the_branch_still_open(parent):
    job = "abc123abc123"
    write(os.path.join(chat_manager._chat_dir(parent), "research", f"{job}.json"), json.dumps({"status": "done"}))
    chat_manager.update_messages(parent, lambda m: m + [{"role": "research", "research_id": job, "topic": "x", "content": ""}])

    b = branches.create_branch(parent)["id"]
    assert os.path.isfile(os.path.join(chat_manager._chat_dir(b), "research", f"{job}.json"))


# --- renaming ---

def test_renaming_a_chat(chat_id):
    assert chat_manager.rename_chat(chat_id, "  new name ")["name"] == "new name"
    assert chat_manager.list_chats()[0]["name"] == "new name"


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_a_chat_cannot_be_renamed_to_nothing_or_something_huge(chat_id, name):
    with pytest.raises(ValueError):
        chat_manager.rename_chat(chat_id, name)
    assert chat_manager.list_chats()[0]["name"] == "test chat"


# --- merging: pick ---

@pytest.fixture
def explored(parent):
    """A branch with two new exchanges, one of them researched on the web."""
    b = branches.create_branch(parent, "dig")["id"]
    exchange(b, "what is the capital of Italy?", "Rome.", looked_up=['web search "capital of Italy"'])
    exchange(b, "and of Portugal?", "Lisbon.")
    return b


def test_picked_answers_join_the_parents_messages_and_memory(parent, explored):
    # branch messages: 0-5 inherited, 6 user, 7 "Rome.", 8 user, 9 "Lisbon."
    result = branches.merge_into_parent(explored, "pick", [7])

    contents = [m["content"] for m in result["messages"]]
    assert contents[6:] == ["Merged 1 exchange from the branch “dig”", "what is the capital of Italy?", "Rome."]
    assert result["added"] == 3 and result["parent_id"] == parent and not result["deleted"]
    assert result["messages"][-1]["merged_from"] == explored
    assert chat_manager.get_messages(parent) == result["messages"]

    turn = chat_manager.get_context(parent)[-1]
    assert (turn["question"], turn["answer"], turn["looked_up"]) == \
        ("what is the capital of Italy?", "Rome.", ['web search "capital of Italy"'])
    assert "and of Portugal?" not in [e["question"] for e in chat_manager.get_context(parent)]


def test_merging_leaves_the_branch_as_it_was(explored):
    before = (chat_manager.get_messages(explored), chat_manager.get_context(explored))
    branches.merge_into_parent(explored, "pick", [7, 9])
    assert (chat_manager.get_messages(explored), chat_manager.get_context(explored)) == before


def test_merged_memory_gets_fresh_ids_so_entries_can_be_deleted_separately(parent, explored):
    branches.merge_into_parent(explored, "pick", [7])
    parent_ids = [e["id"] for e in chat_manager.get_context(parent)]
    assert len(parent_ids) == len(set(parent_ids))
    assert chat_manager.get_context(parent)[-1]["id"] not in [e["id"] for e in chat_manager.get_context(explored)]


def test_several_picked_answers_come_over_in_order(parent, explored):
    result = branches.merge_into_parent(explored, "pick", [9, 7, 7])      # unordered, one repeated
    assert [m["content"] for m in result["messages"]][6:] == [
        "Merged 2 exchanges from the branch “dig”",
        "what is the capital of Italy?", "Rome.", "and of Portugal?", "Lisbon.",
    ]


@pytest.mark.parametrize("indexes", [[], None, [6], [8], [99], [-1], [0]])
def test_picking_something_that_is_not_an_answer_changes_nothing(parent, explored, indexes):
    before = (chat_manager.get_messages(parent), chat_manager.get_context(parent))
    with pytest.raises(ValueError):
        branches.merge_into_parent(explored, "pick", indexes)
    assert (chat_manager.get_messages(parent), chat_manager.get_context(parent)) == before


def test_a_merged_research_card_still_opens_in_the_parent(parent):
    b = branches.create_branch(parent)["id"]
    job = "def456def456"
    write(os.path.join(chat_manager._chat_dir(b), "research", f"{job}.json"), json.dumps({"status": "done"}))
    chat_manager.update_messages(b, lambda m: m + [user("/research x"), athena("see report")])
    # the card itself is only carried by "replace" (picks are user/assistant pairs); check that path
    chat_manager.update_messages(b, lambda m: m + [{"role": "research", "research_id": job, "topic": "x", "content": ""}])
    branches.merge_into_parent(b, "replace")
    assert os.path.isfile(os.path.join(chat_manager._chat_dir(parent), "research", f"{job}.json"))


# --- merging: summary ---

def test_a_summary_of_the_branch_joins_the_parent(parent, explored, monkeypatch):
    chain = fake_compactor(monkeypatch, reply="Italy's capital is Rome; Portugal's is Lisbon.")
    result = branches.merge_into_parent(explored, "summary", focus="capitals")

    assert [m["role"] for m in result["messages"]][6:] == ["system", "assistant"]
    assert result["messages"][-1]["content"] == "Italy's capital is Rome; Portugal's is Lisbon."
    turn = chat_manager.get_context(parent)[-1]
    assert turn["question"] == "Findings from the branch “dig”"
    # only what the branch added is summarized, not what it inherited
    sent = chain.calls[0]["transcript"]
    assert "Rome." in sent and "Lisbon." in sent and "Paris." not in sent
    assert "capitals" in chain.calls[0]["focus"]


def test_a_failed_summary_leaves_the_parent_untouched(parent, explored, monkeypatch):
    def boom(inputs):
        raise RuntimeError("model down")
    monkeypatch.setattr(context_manager, "get_context_compactor", lambda: type("C", (), {"invoke": staticmethod(boom)})())
    before = (chat_manager.get_messages(parent), chat_manager.get_context(parent))
    with pytest.raises(RuntimeError):
        branches.merge_into_parent(explored, "summary")
    assert (chat_manager.get_messages(parent), chat_manager.get_context(parent)) == before


def test_summarizing_a_branch_with_nothing_new_is_refused(parent, monkeypatch):
    fake_compactor(monkeypatch)
    b = branches.create_branch(parent)["id"]
    with pytest.raises(ValueError, match="Nothing"):
        branches.merge_into_parent(b, "summary")


# --- merging: replace ---

def test_replacing_makes_the_parents_conversation_the_branchs(parent, explored):
    write(os.path.join(chat_manager._chat_dir(parent), "files", "later.md"), "added to parent after")
    chat_manager.save_config(parent, {**chat_manager.get_config(parent), "max_agent_steps": 9})
    branch_messages = chat_manager.get_messages(explored)
    branch_context = chat_manager.get_context(explored)

    result = branches.merge_into_parent(explored, "replace")

    assert result["messages"][:-1] == branch_messages
    assert result["messages"][-1]["role"] == "system"
    assert chat_manager.get_context(parent) == branch_context
    assert files_of(parent) == ["later.md", "notes.md"]               # the parent's documents stay
    assert chat_manager.get_config(parent)["max_agent_steps"] == 9    # and so do its settings


def test_the_branch_can_be_deleted_when_it_is_merged(parent, explored):
    grandchild = branches.create_branch(explored, "deeper")["id"]
    result = branches.merge_into_parent(explored, "replace", delete_branch=True)

    assert result["deleted"]
    assert not chat_manager.chat_exists(explored)
    assert chat_manager.get_meta(grandchild)["parent_id"] == parent   # moves up to where the branch merged


def test_replacing_with_an_empty_branch_is_refused_rather_than_erasing_the_parent(parent):
    b = branches.create_branch(parent)["id"]
    chat_manager.save_messages(b, [])
    with pytest.raises(ValueError, match="empty"):
        branches.merge_into_parent(b, "replace")
    assert len(chat_manager.get_messages(parent)) == 6


# --- merging: what can't be merged ---

def test_a_chat_that_is_not_a_branch_has_nowhere_to_merge(parent):
    with pytest.raises(ValueError, match="isn't a branch"):
        branches.merge_into_parent(parent, "pick", [1])


def test_an_unknown_mode_is_refused(explored):
    with pytest.raises(ValueError, match="Unknown"):
        branches.merge_into_parent(explored, "rebase")


def test_a_branch_whose_parent_is_gone_has_nowhere_to_merge(parent, explored, monkeypatch):
    chat_manager.delete_chat(parent)
    with pytest.raises(ValueError):
        branches.merge_into_parent(explored, "pick", [7])


# --- the API ---

def test_the_api_branches_and_merges(client, parent):
    created = client.post(f"/chats/{parent}/branch", json={"name": "api branch"})
    assert created.status_code == 200
    body = created.json()
    assert (body["name"], body["parent_id"], body["branch_point"]) == ("api branch", parent, 6)
    b = body["id"]

    chat_manager.update_messages(b, lambda m: m + [user("side"), athena("answer")])
    merged = client.post(f"/chats/{b}/merge", json={"mode": "pick", "message_indexes": [7], "delete_branch": True})
    assert merged.status_code == 200
    assert [m["content"] for m in merged.json()["messages"]][-2:] == ["side", "answer"]
    assert merged.json()["deleted"] is True
    assert [c["id"] for c in client.get("/chats").json()] == [parent]


def test_the_api_lists_parent_ids(client, parent):
    b = client.post(f"/chats/{parent}/branch").json()["id"]
    listed = {c["id"]: c for c in client.get("/chats").json()}
    assert listed[b]["parent_id"] == parent and listed[parent]["parent_id"] is None


def test_the_api_branches_from_a_message(client, parent):
    body = client.post(f"/chats/{parent}/branch", json={"at_index": 1}).json()
    assert body["branch_point"] == 2
    assert len(client.get(f"/chats/{body['id']}/messages").json()["messages"]) == 2


@pytest.mark.parametrize("payload,status", [({"at_index": 50}, 400), ({"at_index": -1}, 422), ({"name": "x" * 81}, 400)])
def test_the_api_refuses_bad_branch_requests(client, parent, payload, status):
    assert client.post(f"/chats/{parent}/branch", json=payload).status_code == status


def test_the_api_404s_for_unknown_chats(client):
    assert client.post("/chats/nope/branch").status_code == 404
    assert client.post("/chats/nope/merge", json={"mode": "replace"}).status_code == 404
    assert client.patch("/chats/nope", json={"name": "x"}).status_code == 404


def test_the_api_refuses_to_merge_a_chat_that_is_not_a_branch(client, parent):
    response = client.post(f"/chats/{parent}/merge", json={"mode": "replace"})
    assert response.status_code == 400 and "isn't a branch" in response.json()["detail"]


def test_the_api_rejects_an_unknown_merge_mode(client, explored):
    assert client.post(f"/chats/{explored}/merge", json={"mode": "rebase"}).status_code == 422


def test_the_api_reports_a_failed_summary_as_an_error_the_ui_can_act_on(client, parent, explored, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("Error code: 401 - invalid_api_key")
    monkeypatch.setattr(context_manager, "summarize_branch", boom)
    response = client.post(f"/chats/{explored}/merge", json={"mode": "summary"})
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "invalid_key"


def test_the_api_renames_a_chat(client, parent):
    response = client.patch(f"/chats/{parent}", json={"name": "renamed"})
    assert response.status_code == 200 and response.json()["name"] == "renamed"
    assert client.patch(f"/chats/{parent}", json={"name": " "}).status_code == 400


# --- a branch that is still being built ---

def test_a_branch_is_hidden_while_it_is_being_copied(parent, monkeypatch):
    seen = {}
    real = branches._copy_research

    def peek(parent_id, branch_id, kept):
        listed = [chat["id"] for chat in chat_manager.list_chats()]
        seen["listed"] = branch_id in listed
        seen["searchable"] = any(r["chat_id"] == branch_id for r in chat_manager.search_chats("branch"))
        seen["exists"] = chat_manager.chat_exists(branch_id)
        seen["marked"] = chat_manager.get_meta(branch_id).get("building")
        return real(parent_id, branch_id, kept)

    monkeypatch.setattr(branches, "_copy_research", peek)
    meta = branches.create_branch(parent, "side question")
    assert seen == {"listed": False, "searchable": False, "exists": True, "marked": True}
    # and once it is finished it is an ordinary chat
    assert meta["id"] in [chat["id"] for chat in chat_manager.list_chats()]
    assert "building" not in meta and "building" not in chat_manager.get_meta(meta["id"])
    assert any(r["chat_id"] == meta["id"] for r in chat_manager.search_chats("side question"))


def test_a_branch_that_fails_midway_is_removed_and_never_listed(parent, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(branches, "_copy_research", boom)
    before = [chat["id"] for chat in chat_manager.list_chats()]
    with pytest.raises(RuntimeError):
        branches.create_branch(parent, "doomed")
    assert [chat["id"] for chat in chat_manager.list_chats()] == before
    assert sorted(os.listdir(chat_manager.CHATS_ROOT)) == sorted(before)      # no folder left behind


def test_startup_discards_a_branch_a_crash_left_unfinished(parent):
    survivor = branches.create_branch(parent, "complete one")
    crashed = chat_manager.create_chat("half", copy_from=parent, parent_id=parent, branch_point=2, building=True)
    assert chat_manager.discard_unfinished_chats() == 1
    assert not chat_manager.chat_exists(crashed["id"])
    assert chat_manager.chat_exists(survivor["id"]) and chat_manager.chat_exists(parent)
    assert chat_manager.discard_unfinished_chats() == 0


def test_a_chat_that_is_not_marked_is_never_discarded_at_startup(chat_id):
    assert chat_manager.discard_unfinished_chats() == 0
    assert chat_manager.chat_exists(chat_id)


def test_finish_building_is_idempotent(parent):
    crashed = chat_manager.create_chat("half", parent_id=parent, branch_point=0, building=True)
    assert chat_manager.finish_building(crashed["id"]).get("building") is None
    assert chat_manager.finish_building(crashed["id"]).get("building") is None
    assert crashed["id"] in [chat["id"] for chat in chat_manager.list_chats()]
