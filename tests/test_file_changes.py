"""
The list of files a reply created or changed, with a diff for each (file_changes.py), and how it reaches the
browser: a `files` event after each step that touched a file, saved with the reply.
"""

import os

import pytest

import agent
import agent_tools
import chat_manager
import file_changes
from file_changes import FileChanges
from fakes import ScriptedLLM, call, text
from turns import TurnRecorder


@pytest.fixture(autouse=True)
def no_documents(monkeypatch):
    monkeypatch.setattr(agent_tools, "has_documents", lambda path: False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    (folder / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(agent_tools, "real_home", lambda: str(folder))
    return str(folder)


def entry(changes, path):
    return next(item for item in changes.summary() if item["path"] == path)


class TestDiffs:
    def test_a_new_file_is_all_additions(self):
        changes = FileChanges()
        changes.record("/w/a.py", "a.py", None, "x = 1\ny = 2\n")
        item = entry(changes, "a.py")
        assert item["status"] == "created" and (item["added"], item["removed"]) == (2, 0)
        assert "+x = 1" in item["diff"] and "--- " not in item["diff"] and "+++ " not in item["diff"]

    def test_an_edited_file_shows_what_went_and_what_came(self):
        changes = FileChanges()
        changes.record("/w/a.py", "a.py", "a\nb\nc\n", "a\nB\nc\n")
        item = entry(changes, "a.py")
        assert item["status"] == "modified" and (item["added"], item["removed"]) == (1, 1)
        assert "-b" in item["diff"].splitlines() and "+B" in item["diff"].splitlines()

    def test_two_edits_to_one_file_are_one_diff_from_the_start_to_the_end(self):
        changes = FileChanges()
        changes.record("/w/a.py", "a.py", "one\n", "two\n")
        changes.record("/w/a.py", "a.py", "two\n", "three\n")
        item = entry(changes, "a.py")
        assert "-one" in item["diff"] and "+three" in item["diff"] and "two" not in item["diff"]

    def test_a_file_changed_and_changed_back_is_not_listed(self):
        changes = FileChanges()
        changes.record("/w/a.py", "a.py", "same\n", "other\n")
        changes.record("/w/a.py", "a.py", "other\n", "same\n")
        assert changes.summary() == []

    def test_a_deleted_file_is_listed_as_deleted(self):
        changes = FileChanges()
        changes.record("/w/a.py", "a.py", "gone\n", None)
        assert entry(changes, "a.py")["status"] == "deleted"

    def test_a_file_with_no_text_is_listed_without_a_diff(self):
        changes = FileChanges()
        changes.record("/w/p.png", "p.png", None, file_changes.BINARY)
        item = entry(changes, "p.png")
        assert item["status"] == "created" and "diff" not in item and "no text" in item["note"]

    def test_a_huge_diff_is_listed_without_its_text(self):
        changes = FileChanges()
        changes.record("/w/big.txt", "big.txt", None, "line\n" * 20000)
        item = entry(changes, "big.txt")
        assert item["added"] == 20000 and "diff" not in item and "too long" in item["note"]

    def test_take_hands_the_list_over_only_when_something_changed(self):
        changes = FileChanges()
        assert changes.take() is None
        changes.record("/w/a.py", "a.py", None, "x\n")
        assert len(changes.take()) == 1
        assert changes.take() is None


class TestWatchingTheWorkspace:
    def test_new_changed_and_removed_files_are_found(self, tmp_path):
        (tmp_path / "keep.txt").write_text("same\n")
        (tmp_path / "edit.py").write_text("a\n")
        (tmp_path / "gone.txt").write_text("bye\n")
        before = file_changes.snapshot_workspace(str(tmp_path))
        (tmp_path / "edit.py").write_text("a\nb\n")
        os.utime(tmp_path / "edit.py", (1, 1))
        (tmp_path / "gone.txt").unlink()
        (tmp_path / "new.py").write_text("n\n")
        changes = FileChanges()
        changes.record_workspace(str(tmp_path), str(tmp_path), before, file_changes.snapshot_workspace(str(tmp_path)))
        statuses = {item["path"]: item["status"] for item in changes.summary()}
        assert statuses == {"edit.py": "modified", "gone.txt": "deleted", "new.py": "created"}

    def test_the_runners_own_scratch_files_and_hidden_folders_are_ignored(self, tmp_path):
        (tmp_path / "_athena_run.py").write_text("x")
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "f.txt").write_text("x")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "m.pyc").write_text("x")
        assert file_changes.snapshot_workspace(str(tmp_path)) == {}

    def test_paths_are_shown_relative_to_the_workspace_or_as_in_the_home_folder(self, tmp_path):
        workspace, home = tmp_path / "w", tmp_path / "h"
        (workspace / "sub").mkdir(parents=True)
        (home / "Desktop").mkdir(parents=True)
        assert file_changes.label_for(str(workspace / "sub" / "a.py"), str(workspace), str(home)) == os.path.join("sub", "a.py")
        assert file_changes.label_for(str(home / "Desktop" / "b.py"), str(workspace), str(home)) == "~/Desktop/b.py"
        assert file_changes.label_for("/elsewhere/c.py", str(workspace), str(home)) == "/elsewhere/c.py"


REPLY = "Here you go.\n\n```python\nprint('hi')\n```\n"


def run(chat_id, monkeypatch, *script, mode="bypass", question="put it in a file"):
    llm = ScriptedLLM(*script)
    monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
    config = {**chat_manager.get_config(chat_id), "permission_mode": mode}
    events = []
    for event in agent.run_agent(chat_id, question, [], config):
        events.append(event)
        if event["type"] == "permission_request":
            agent_tools.resolve_permission_request(chat_id, event["id"], True)
    return events


class TestInTheAgentLoop:
    def test_saving_a_file_sends_the_list_right_after_the_step_result(self, chat_id, home, monkeypatch):
        chat_manager.save_messages(chat_id, [{"role": "user", "content": "write it"}, {"role": "assistant", "content": REPLY}])
        events = run(chat_id, monkeypatch, call("save_code", {"path": "~/Desktop/hi.py"}), text("saved"))
        kinds = [e["type"] for e in events]
        assert kinds.index("files") == kinds.index("step_result") + 1
        listed = next(e for e in events if e["type"] == "files")["files"]
        assert [(f["path"], f["status"], f["added"]) for f in listed] == [("~/Desktop/hi.py", "created", 1)]

    def test_replacing_a_file_shows_the_edit(self, chat_id, home, monkeypatch):
        target = os.path.join(home, "Desktop", "hi.py")
        with open(target, "w") as f:
            f.write("print('old')\n")
        chat_manager.save_messages(chat_id, [{"role": "user", "content": "write it"}, {"role": "assistant", "content": REPLY}])
        # replacing a file is asked about even in bypass mode; run() says yes
        events = run(chat_id, monkeypatch, call("save_code", {"path": "~/Desktop/hi.py", "overwrite": True}), text("done"))
        listed = next(e for e in events if e["type"] == "files")["files"]
        assert listed[0]["status"] == "modified" and "-print('old')" in listed[0]["diff"] and "+print('hi')" in listed[0]["diff"]

    def test_running_code_that_writes_a_file_lists_it(self, chat_id, home, monkeypatch):
        code = "open('out.txt', 'w').write('hello\\n')"
        events = run(chat_id, monkeypatch, call("run_python", {"code": code}), text("done"), question="run some python")
        listed = next(e for e in events if e["type"] == "files")["files"]
        assert [(f["path"], f["status"]) for f in listed] == [("out.txt", "created")]

    def test_a_step_that_touches_no_file_sends_no_list(self, chat_id, home, monkeypatch):
        events = run(chat_id, monkeypatch, call("run_python", {"code": "print(1)"}), text("one"), question="run some python")
        assert "files" not in [e["type"] for e in events]

    def test_a_file_that_failed_to_save_is_not_listed(self, chat_id, home, monkeypatch):
        chat_manager.save_messages(chat_id, [{"role": "user", "content": "write it"}, {"role": "assistant", "content": REPLY}])
        events = run(chat_id, monkeypatch, call("save_code", {"path": "/etc/hi.py"}), text("could not"))
        assert "files" not in [e["type"] for e in events]


class TestSavedWithTheReply:
    def test_the_recorder_keeps_the_latest_list_with_the_reply(self, chat_id):
        recorder = TurnRecorder(chat_id, "q")
        recorder.begin()
        first = [{"path": "a.py", "status": "created", "added": 1, "removed": 0, "diff": "+x"}]
        both = first + [{"path": "b.py", "status": "created", "added": 1, "removed": 0, "diff": "+y"}]
        recorder.apply({"type": "files", "files": first})
        recorder.apply({"type": "files", "files": both})
        recorder.apply({"type": "complete", "response_time": 1.0})
        assert chat_manager.get_messages(chat_id)[-1]["files"] == both


class TestCodeThatWritesToTheHomeFolder:
    def test_paths_spelled_out_in_the_code_are_found(self, tmp_path):
        home = str(tmp_path)
        code = f"open('{home}/Desktop/app.py', 'w'); open(\"~/Documents/n.txt\", 'w'); open('/etc/x', 'w'); open('out.txt')"
        assert file_changes.paths_written_in(code, home) == [f"{home}/Desktop/app.py", f"{home}/Documents/n.txt"]

    def test_the_home_folder_itself_hidden_folders_and_library_are_not_watched(self, tmp_path):
        home = str(tmp_path)
        code = f"'{home}' '{home}/Desktop' '{home}/.ssh/id' '{home}/Library/x/y'"
        assert file_changes.paths_written_in(code, home) == []

    def test_a_file_the_code_creates_on_the_desktop_is_listed(self, chat_id, home, monkeypatch):
        target = os.path.join(home, "Desktop", "game.py")
        code = f"open({target!r}, 'w').write('print(1)\\n')"
        events = run(chat_id, monkeypatch, call("run_python", {"code": code}), text("done"), question="run some python")
        listed = next(e for e in events if e["type"] == "files")["files"]
        assert [(f["path"], f["status"], f["added"]) for f in listed] == [("~/Desktop/game.py", "created", 1)]

    def test_a_file_in_a_folder_the_code_makes_is_listed_too(self, chat_id, home, monkeypatch):
        target = os.path.join(home, "Desktop", "proj", "a.py")
        code = f"import os\nos.makedirs({os.path.dirname(target)!r})\nopen({target!r}, 'w').write('x = 1\\n')"
        events = run(chat_id, monkeypatch, call("run_python", {"code": code}), text("done"), question="run some python")
        listed = next(e for e in events if e["type"] == "files")["files"]
        assert [f["path"] for f in listed] == ["~/Desktop/proj/a.py"]
