"""
Saving a code block from the previous reply to a file on the user's computer (the save_code tool),
and the changes around it: tool arguments that fail say which one, a repeated failing call says what
failed, and the model is told where the user's real home folder is.
"""

import os

import pytest

import agent
import agent_tools
import chat_manager
import llm_prompts
from agent_tools import SaveError, code_blocks, friendly_tool_error, resolve_save_path
from fakes import ScriptedLLM, call, text

BACKTESTER = 'def backtest(prices):\n    """Run it."""\n    return sum(prices)\n'
REPLY = f"Here is a backtester.\n\n```python\n{BACKTESTER}```\n\nIt sums the prices."


@pytest.fixture(autouse=True)
def no_documents(monkeypatch):
    monkeypatch.setattr(agent_tools, "has_documents", lambda path: False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    (folder / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(agent_tools, "real_home", lambda: str(folder))
    return str(folder)


@pytest.fixture
def workspace(chat_id):
    return chat_manager.get_workspace_path(chat_id)


def say(chat_id, *messages):
    chat_manager.save_messages(chat_id, [{"role": role, "content": content} for role, content in messages])


def make_box(chat_id, mode="ask"):
    config = {**chat_manager.get_config(chat_id), "permission_mode": mode}
    return agent_tools.ToolBox(chat_id, config)


# --- finding the code ---

class TestCodeBlocks:
    def test_blocks_are_found_in_order_without_their_fences(self):
        text = "a\n```python\nx = 1\n```\nb\n```\ny = 2\nz = 3\n```\n"
        assert code_blocks(text) == ["x = 1", "y = 2\nz = 3"]

    def test_text_with_no_fences_has_none(self):
        assert code_blocks("just words, and `inline` code") == []
        assert code_blocks("") == [] and code_blocks(None) == []

    def test_a_language_tag_is_not_part_of_the_code(self):
        assert code_blocks("```js\nlet a = 1\n```") == ["let a = 1"]

    def test_triple_quotes_inside_code_do_not_end_the_block(self):
        assert code_blocks(f"```python\n{BACKTESTER}```") == [BACKTESTER.rstrip("\n")]


# --- where it may go ---

class TestPaths:
    @pytest.fixture
    def places(self, tmp_path):
        home = tmp_path / "me"
        workspace = home / "work" / "workspace"
        (home / "Desktop").mkdir(parents=True)
        workspace.mkdir(parents=True)
        return str(home), str(workspace)

    def resolve(self, places, path, **kw):
        home, workspace = places
        return resolve_save_path(path, workspace, home, **kw)

    def test_tilde_means_the_real_home_not_the_workspace(self, places):
        home, _ = places
        assert self.resolve(places, "~/Desktop/athenastuff/backtester.py") == f"{home}/Desktop/athenastuff/backtester.py"

    def test_a_full_path_inside_home_is_fine(self, places):
        home, _ = places
        assert self.resolve(places, f"{home}/Documents/x.py") == f"{home}/Documents/x.py"

    def test_a_relative_path_means_the_workspace(self, places):
        _, workspace = places
        assert self.resolve(places, "notes/x.py") == f"{workspace}/notes/x.py"

    def test_dots_are_tidied_but_cannot_climb_out(self, places):
        home, _ = places
        assert self.resolve(places, "~/Desktop/./a/../b.py") == f"{home}/Desktop/b.py"
        with pytest.raises(SaveError, match="inside the user's home"):
            self.resolve(places, "~/../../etc/passwd")

    @pytest.mark.parametrize("path", ["/etc/cron.d/job", "/usr/local/bin/tool", "/tmp/x.py", "/"])
    def test_nothing_outside_the_home_folder(self, places, path):
        with pytest.raises(SaveError):
            self.resolve(places, path)

    @pytest.mark.parametrize("path", ["~/.ssh/authorized_keys", "~/.zshrc", "~/.config/x/y.py", "~/Desktop/.hidden/a.py",
                                      "~/Library/LaunchAgents/evil.plist", "~/Library/x.py"])
    def test_no_hidden_folders_and_no_library(self, places, path):
        with pytest.raises(SaveError, match="hidden folder or the Library"):
            self.resolve(places, path)

    def test_another_users_tilde_is_refused(self, places):
        with pytest.raises(SaveError, match="only your own home"):
            self.resolve(places, "~root/x.py")

    def test_a_symlink_cannot_lead_out_of_home(self, places, tmp_path):
        home, _ = places
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        os.symlink(outside, os.path.join(home, "Desktop", "shortcut"))
        with pytest.raises(SaveError, match="user's home folder"):
            self.resolve(places, "~/Desktop/shortcut/x.py")

    def test_empty_and_folder_paths_are_refused_with_an_example(self, places):
        with pytest.raises(SaveError, match="full path of the file"):
            self.resolve(places, "  ")
        with pytest.raises(SaveError, match="that is a folder"):
            self.resolve(places, "~/Desktop/athenastuff/")
        with pytest.raises(SaveError, match="is a folder"):
            self.resolve(places, "~/Desktop")

    def test_an_existing_file_is_only_replaced_on_request(self, places):
        home, _ = places
        existing = os.path.join(home, "Desktop", "a.py")
        open(existing, "w").write("mine")
        with pytest.raises(SaveError, match="already exists.*ask the user"):
            self.resolve(places, "~/Desktop/a.py")
        assert self.resolve(places, "~/Desktop/a.py", overwrite=True) == existing


# --- picking what to save ---

class TestPlan:
    def plan(self, chat_id, **args):
        return make_box(chat_id).save_plan({"path": "~/Desktop/x/backtester.py", **args})

    def test_it_takes_the_code_from_the_last_reply_that_has_some(self, chat_id, home):
        say(chat_id, ("user", "create a backtester"), ("assistant", REPLY), ("user", "thanks"), ("assistant", "you're welcome"))
        target, content = self.plan(chat_id)
        assert target == f"{home}/Desktop/x/backtester.py" and content == BACKTESTER

    def test_a_later_reply_with_code_wins_over_an_earlier_one(self, chat_id, home):
        say(chat_id, ("assistant", "```\nold = 1\n```"), ("assistant", "```\nnew = 2\n```"))
        assert self.plan(chat_id)[1] == "new = 2\n"

    def test_code_a_user_pasted_is_not_saved_as_if_the_assistant_wrote_it(self, chat_id, home):
        say(chat_id, ("user", "```\nmine = 1\n```"), ("assistant", "ok"))
        with pytest.raises(SaveError, match="none of your earlier replies"):
            self.plan(chat_id)

    def test_with_several_blocks_the_longest_is_the_default_and_a_number_picks_one(self, chat_id, home):
        say(chat_id, ("assistant", "```\nshort\n```\n\n```\nthe long one\nspans lines\n```"))
        assert self.plan(chat_id)[1] == "the long one\nspans lines\n"
        assert self.plan(chat_id, block=1)[1] == "short\n"
        with pytest.raises(SaveError, match="2 code block"):
            self.plan(chat_id, block=3)

    def test_a_chat_with_no_code_says_what_to_do_instead(self, chat_id, home):
        say(chat_id, ("assistant", "no code here"))
        with pytest.raises(SaveError, match="write the code in a reply first"):
            self.plan(chat_id)

    def test_a_block_number_that_is_not_a_number_is_a_clear_error(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        with pytest.raises(SaveError, match="block must be a number"):
            self.plan(chat_id, block="first")


# --- writing ---

class TestSaving:
    def test_it_writes_the_file_and_makes_the_folders(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        result = make_box(chat_id).save_code("~/Desktop/athenastuff/backtester.py")
        target = f"{home}/Desktop/athenastuff/backtester.py"
        assert result.startswith("saved") and target in result
        assert open(target).read() == BACKTESTER
        assert os.listdir(f"{home}/Desktop") == ["athenastuff"]

    def test_the_saved_code_is_exactly_what_was_in_the_reply(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        make_box(chat_id).save_code("~/Desktop/a.py")
        assert open(f"{home}/Desktop/a.py").read() == BACKTESTER       # nothing retyped, nothing lost

    def test_refusals_come_back_as_errors_the_model_can_read_not_exceptions(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        assert make_box(chat_id).save_code("/etc/passwd").startswith("error: files can only be saved inside")
        assert make_box(chat_id).save_code("~/.ssh/config").startswith("error: won't save into a hidden folder")
        assert not os.path.exists("/etc/passwd.py")

    def test_an_existing_file_is_kept_unless_overwrite_is_true(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        target = f"{home}/Desktop/a.py"
        open(target, "w").write("precious")
        assert "already exists" in make_box(chat_id).save_code("~/Desktop/a.py")
        assert open(target).read() == "precious"
        assert make_box(chat_id).save_code("~/Desktop/a.py", overwrite=True).startswith("saved")
        assert open(target).read() == BACKTESTER

    def test_a_folder_that_cannot_be_written_is_reported(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        os.chmod(f"{home}/Desktop", 0o500)
        try:
            assert make_box(chat_id).save_code("~/Desktop/a.py").startswith("error: couldn't write")
        finally:
            os.chmod(f"{home}/Desktop", 0o700)


# --- who is asked ---

class TestPermission:
    def test_ask_mode_shows_the_path_and_the_whole_file_first(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        kind, shown = make_box(chat_id).permission_prompt("save_code", {"path": "~/Desktop/x/b.py"})
        assert kind == "save"
        assert shown.startswith(f"save to {home}/Desktop/x/b.py\n")
        assert BACKTESTER.strip() in shown and "characters" in shown.splitlines()[1]

    def test_nothing_to_approve_when_it_could_not_run_anyway(self, chat_id, home):
        say(chat_id, ("assistant", "no code"))
        assert make_box(chat_id).permission_prompt("save_code", {"path": "~/Desktop/a.py"}) is None      # the tool explains
        say(chat_id, ("assistant", REPLY))
        assert make_box(chat_id).permission_prompt("save_code", {"path": "/etc/passwd"}) is None

    def test_code_is_still_shown_as_code(self, chat_id, home):
        assert make_box(chat_id).permission_prompt("run_python", {"code": "print(1)"}) == ("code", "print(1)")

    def test_bypass_and_other_tools_need_no_approval(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        assert make_box(chat_id, "bypass").permission_prompt("save_code", {"path": "~/Desktop/a.py"}) is None
        assert make_box(chat_id).permission_prompt("calculator", {"expression": "1"}) is None

    def test_safe_mode_does_not_even_offer_the_tool(self, chat_id, home):
        names = {tool.name for tool in make_box(chat_id, "safe").tools()}
        assert "save_code" not in names and "run_python" not in names
        assert "save_code" in {tool.name for tool in make_box(chat_id, "ask").tools()}


def drive(chat_id, monkeypatch, approve, mode="ask", path="~/Desktop/athenastuff/backtester.py"):
    llm = ScriptedLLM(call("save_code", {"path": path}), text("saved it"))
    monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
    config = {**chat_manager.get_config(chat_id), "permission_mode": mode}
    events = []
    for event in agent.run_agent(chat_id, "put it in a folder called athenastuff in my desktop", [], config):
        events.append(event)
        if event["type"] == "permission_request":
            assert agent_tools.resolve_permission_request(chat_id, event["id"], approve)
    return events


class TestInTheAgentLoop:
    def test_ask_mode_asks_with_a_save_request_then_writes(self, chat_id, home, monkeypatch):
        say(chat_id, ("user", "create a backtester"), ("assistant", REPLY))
        events = drive(chat_id, monkeypatch, approve=True)
        request = next(e for e in events if e["type"] == "permission_request")
        assert request["kind"] == "save" and request["tool"] == "save_code"
        assert f"{home}/Desktop/athenastuff/backtester.py" in request["code"]
        result = next(e for e in events if e["type"] == "step_result")
        assert result["status"] == "ok"
        assert open(f"{home}/Desktop/athenastuff/backtester.py").read() == BACKTESTER

    def test_saying_no_writes_nothing_and_tells_the_model_it_was_a_file(self, chat_id, home, monkeypatch):
        say(chat_id, ("assistant", REPLY))
        events = drive(chat_id, monkeypatch, approve=False)
        result = next(e for e in events if e["type"] == "step_result")
        assert result["status"] == "denied" and "did not allow this file to be saved" in result["result"]
        assert not os.path.exists(f"{home}/Desktop/athenastuff")

    def test_bypass_saves_without_asking(self, chat_id, home, monkeypatch):
        say(chat_id, ("assistant", REPLY))
        events = drive(chat_id, monkeypatch, approve=True, mode="bypass")
        assert "permission_request" not in [e["type"] for e in events]
        assert os.path.exists(f"{home}/Desktop/athenastuff/backtester.py")

    def test_a_refused_path_is_not_asked_about_and_reports_why(self, chat_id, home, monkeypatch):
        say(chat_id, ("assistant", REPLY))
        events = drive(chat_id, monkeypatch, approve=True, path="/etc/cron.d/x")
        assert "permission_request" not in [e["type"] for e in events]
        result = next(e for e in events if e["type"] == "step_result")
        assert result["status"] == "error" and "inside the user's home" in result["result"]

    def test_running_code_is_still_asked_about_as_code(self, chat_id, home, monkeypatch):
        monkeypatch.setattr(agent_tools, "run_python_code", lambda code, workspace: "1")
        llm = ScriptedLLM(call("run_python", {"code": "print(1)"}), text("done"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "ask"}
        events = []
        for event in agent.run_agent(chat_id, "run some python", [], config):
            events.append(event)
            if event["type"] == "permission_request":
                agent_tools.resolve_permission_request(chat_id, event["id"], True)
        assert next(e for e in events if e["type"] == "permission_request")["kind"] == "code"


# --- the failures in the transcript that started this ---

class TestFailingCalls:
    def test_a_call_with_no_code_says_which_argument_is_missing(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(call("run_python", {}), text("sorry"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "bypass"}
        events = list(agent.run_agent(chat_id, "run some python to write a file", [], config))
        result = next(e for e in events if e["type"] == "step_result")["result"]
        assert "`code` is missing" in result and "every required argument" in result
        assert "validation error" not in result and "pydantic" not in result

    def test_friendly_errors_name_wrong_types_too(self):
        tool = [t for t in make_tools() if t.name == "run_python"][0]
        try:
            tool.invoke({"code": 5})
        except Exception as e:
            message = friendly_tool_error("run_python", e)
        assert "`code` is not valid" in message

    def test_other_errors_pass_through_unchanged(self):
        assert friendly_tool_error("x", RuntimeError("boom")) == "error: boom"

    def test_repeating_a_failed_call_says_what_failed_not_just_that_it_was_repeated(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(call("run_python", {}), call("run_python", {}, "call-2"), call("run_python", {}, "call-3"), text("giving up"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "bypass"}
        results = [e["result"] for e in agent.run_agent(chat_id, "run some python to write a file", [], config) if e["type"] == "step_result"]
        assert "`code` is missing" in results[0]
        for repeat in results[1:]:
            assert "already failed" in repeat and "`code` is missing" in repeat and "change the arguments" in repeat
            assert "use the earlier result" not in repeat           # the old advice, which couldn't help here

    def test_repeating_a_call_that_worked_still_says_to_use_the_earlier_result(self, chat_id, monkeypatch):
        same = {"expression": "2+2"}
        llm = ScriptedLLM(call("calculator", same), call("calculator", same, "call-2"), text("4"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = chat_manager.get_config(chat_id)
        results = [e["result"] for e in agent.run_agent(chat_id, "2+2", [], config) if e["type"] == "step_result"]
        assert "use the earlier result" in results[1]


def make_tools():
    box = agent_tools.ToolBox.__new__(agent_tools.ToolBox)
    box.chat_id = "x"
    box.config = {"permission_mode": "ask", "web_search_enabled": False}
    box.code_enabled = True
    box.chroma_db_path = box.files_path = "/tmp"
    box.retrieved_docs, box.context_tools_allowed, box._seen_chunks = [], set(), set()
    return box.tools()


# --- telling the model where things are ---

class TestWhatTheModelIsTold:
    def test_the_run_python_description_names_the_real_home_and_desktop(self, chat_id, home):
        description = {t.name: t.description for t in make_box(chat_id).tools()}["run_python"]
        assert f"their home is {home}" in description and f"{home}/Desktop" in description
        assert "`~` in the code means that folder, not the user's home" in description

    def test_the_save_description_gives_an_example_path_on_this_machine(self, chat_id, home):
        description = {t.name: t.description for t in make_box(chat_id).tools()}["save_code"]
        assert f"{home}/Desktop/project/file.py" in description and "without retyping" in description

    def test_the_prompt_hints_carry_the_home_folder_and_never_leave_a_placeholder(self, home):
        lines = llm_prompts.tool_hint_lines({"run_python", "save_code"}, home)
        assert home in lines and "{home}" not in lines
        assert "Never claim you cannot reach the user's folders" in lines

    def test_the_hint_for_running_code_no_longer_forbids_saving_a_file(self):
        hint = llm_prompts.agent_tool_hints["run_python"]
        assert "use full paths for files the user wants to find" in hint

    def test_a_tool_that_is_not_offered_is_not_described(self, home):
        assert "save_code" not in llm_prompts.tool_hint_lines({"calculator"}, home)

    def test_the_system_prompt_for_a_run_contains_the_home_folder(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(text("hi"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        monkeypatch.setattr(agent, "real_home", lambda: home)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "ask"}
        list(agent.run_agent(chat_id, "save the code to my desktop", [], config))
        system = llm.calls[0][0].content
        assert f"{home}/Desktop" in system and "save_code" in system

    def test_research_is_never_offered_saving(self, chat_id, monkeypatch, home):
        import research
        seen = {}

        def loop(messages, toolbox, max_steps, include_context_tools=True, **options):
            seen.update(options)
            yield {"type": "step", "id": 1, "tool": "web_search", "args": {}, "thought": ""}
            return "note"

        monkeypatch.setattr(research, "agent_loop", loop)
        monkeypatch.setattr(research, "get_research_planner", lambda: type("P", (), {"invoke": lambda self, i: "1. a long enough question"})())
        monkeypatch.setattr(research, "get_research_writer", lambda: type("W", (), {"invoke": lambda self, i: "REPORT"})())
        job = {"id": "abc123def456", "topic": "t", "status": "running", "phase": "planning", "current": "", "plan": [],
               "notes": [], "report": "", "error": "", "error_code": "", "created_at": "", "finished_at": ""}
        research._save(chat_id, job)
        import threading
        research._active[job["id"]] = threading.Event()
        research._run(chat_id, job, research._active[job["id"]], lambda e: ("error", str(e)))
        assert seen["withhold_tools"] == frozenset({"save_code"})


# --- when saving is offered at all ---

class TestWantsSave:
    @pytest.mark.parametrize("message", [
        "put it in a folder called athenastuff in desktop",       # the message that started all this
        "save it to my desktop", "save that as backtester.py", "export the code", "write that to a file",
        "store it in my documents folder", "copy it into ~/Documents/work", "can you drop it on my desktop",
        "put it in /Users/me/Desktop/x",
    ])
    def test_messages_about_saving_a_file(self, message):
        assert agent.wants_save(message)

    @pytest.mark.parametrize("message", [
        "create a backtester system in python", "what is the capital of France?", "write a poem about spring",
        "summarize my homework", "explain how the code works", "put the kettle on", "run a quick check of recursion",
    ])
    def test_everything_else_is_not(self, message):
        assert not agent.wants_save(message)

    def test_a_yes_to_the_assistants_own_offer_to_save_counts(self):
        history = [("create a backtester", "Here it is. Want me to save it to your Desktop?")]
        assert agent.wants_save("sure", history, accepted_offer=True)
        assert not agent.wants_save("sure", history, accepted_offer=False)
        assert not agent.wants_save("sure", [("hi", "hello")], accepted_offer=True)

    def test_the_tool_is_not_offered_for_a_message_that_is_not_about_saving(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(text("here you go"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "bypass"}
        list(agent.run_agent(chat_id, "create a backtester system in python", [], config))
        assert "save_code" not in llm.calls[0][0].content          # not described to the model
        assert "save_code" not in llm.bound_names                    # and not callable

    def test_a_model_that_calls_it_anyway_cannot_save_anything(self, chat_id, home, monkeypatch):
        say(chat_id, ("assistant", REPLY))
        llm = ScriptedLLM(call("save_code", {"path": "~/Desktop/surprise.py"}), text("ok"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "bypass"}
        events = list(agent.run_agent(chat_id, "create a backtester system in python", [], config))
        assert "no tool called" in next(e for e in events if e["type"] == "step_result")["result"]
        assert not os.path.exists(f"{home}/Desktop/surprise.py")

    def test_it_is_offered_when_the_message_asks_to_save(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(text("done"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "ask"}
        list(agent.run_agent(chat_id, "put it in a folder called athenastuff in desktop", [], config))
        assert "save_code" in llm.calls[0][0].content


# --- a reply cut off by the length limit (the real cause behind the transcript) ---

from langchain_core.messages import AIMessageChunk  # noqa: E402

CUT_OFF = "Here is the backtester:\n\n```python\nclass Backtester:\n    def run(self):\n        if not data.index.is_monotonic_increas"


class TestCutOffReplies:
    def test_an_unclosed_block_is_recognised(self):
        assert agent_tools.has_unfinished_block(CUT_OFF)
        assert not agent_tools.has_unfinished_block(REPLY)
        assert not agent_tools.has_unfinished_block("no code at all")
        assert not agent_tools.has_unfinished_block("")

    def test_saving_it_says_it_was_cut_off_not_that_there_is_no_code(self, chat_id, home):
        say(chat_id, ("user", "create a backtester"), ("assistant", CUT_OFF))
        result = make_box(chat_id).save_code("~/Desktop/athenastuff/backtester.py")
        assert "cut off" in result and "length limit" in result and "incomplete" in result
        assert "none of your earlier replies" not in result
        assert not os.path.exists(f"{home}/Desktop/athenastuff")      # nothing half-written

    def test_an_earlier_complete_block_is_still_found_past_a_cut_off_reply(self, chat_id, home):
        # the newest reply with code decides: it was cut off, so don't quietly save the older one instead
        say(chat_id, ("assistant", REPLY), ("assistant", CUT_OFF))
        assert "cut off" in make_box(chat_id).save_code("~/Desktop/a.py")

    def test_a_cut_off_reply_does_not_stop_a_later_complete_one_being_saved(self, chat_id, home):
        say(chat_id, ("assistant", CUT_OFF), ("assistant", REPLY))
        assert make_box(chat_id).save_code("~/Desktop/a.py").startswith("saved")


def turn_with(metadata, content="partial answer"):
    return [AIMessageChunk(content=content), AIMessageChunk(content="", response_metadata=metadata)]


class TestTheLengthNotice:
    @pytest.mark.parametrize("metadata", [
        {"done_reason": "length"},            # Ollama
        {"finish_reason": "length"},          # OpenAI
        {"stop_reason": "max_tokens"},        # Claude
    ])
    def test_a_reply_that_ran_out_of_tokens_even_after_continuing_says_so(self, chat_id, monkeypatch, metadata):
        llm = ScriptedLLM(*[turn_with(metadata, f"part {n} ") for n in range(1 + agent.MAX_CONTINUATIONS)])
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        events = list(agent.run_agent(chat_id, "tell me about backtesting", [], chat_manager.get_config(chat_id)))
        notices = [e["message"] for e in events if e["type"] == "notice"]
        assert notices == [agent.TRUNCATED_NOTICE] and "cut off" in notices[0]

    @pytest.mark.parametrize("metadata", [{"done_reason": "stop"}, {"finish_reason": "stop"}, {"stop_reason": "end_turn"}, {}])
    def test_a_reply_that_finished_says_nothing(self, chat_id, monkeypatch, metadata):
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: ScriptedLLM(turn_with(metadata)))
        events = list(agent.run_agent(chat_id, "tell me about backtesting", [], chat_manager.get_config(chat_id)))
        assert [e for e in events if e["type"] == "notice"] == []

    def test_the_notice_arrives_before_the_end_of_the_reply(self, chat_id, monkeypatch):
        llm = ScriptedLLM(*[turn_with({"done_reason": "length"}, f"part {n} ") for n in range(1 + agent.MAX_CONTINUATIONS)])
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        kinds = [e["type"] for e in agent.run_agent(chat_id, "tell me about backtesting", [], chat_manager.get_config(chat_id))]
        assert kinds.index("notice") < kinds.index("complete")

    def test_a_tool_turn_that_hit_the_limit_is_not_mistaken_for_the_answer(self, chat_id, monkeypatch):
        # only the turn that ends the reply can be a cut-off answer
        turns = [call("calculator", {"expression": "1+1"}), turn_with({"done_reason": "stop"}, "2")]
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: ScriptedLLM(*turns))
        events = list(agent.run_agent(chat_id, "what is 1+1", [], chat_manager.get_config(chat_id)))
        assert [e for e in events if e["type"] == "notice"] == []

    def test_a_response_with_no_metadata_at_all_is_fine(self):
        assert agent.hit_length_limit(None) is False
        assert agent.hit_length_limit(AIMessageChunk(content="x")) is False


# --- a call that cannot run is never put to the user ---

class TestNothingToApprove:
    @pytest.mark.parametrize("args", [{}, {"code": ""}, {"code": "   \n"}, {"code": None}, {"code": 5}, {"cod": "print(1)"}])
    def test_running_empty_or_malformed_code_is_never_asked_about(self, chat_id, home, args):
        assert make_box(chat_id).permission_prompt("run_python", args) is None

    def test_real_code_is_still_asked_about(self, chat_id, home):
        assert make_box(chat_id).permission_prompt("run_python", {"code": "print(1)"}) == ("code", "print(1)")

    def test_in_the_loop_an_empty_call_fails_straight_away_with_no_prompt(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(call("run_python", {}), text("I will put the code in my reply"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "ask"}
        events = list(agent.run_agent(chat_id, "create a backtester system in python", [], config))
        assert "permission_request" not in [e["type"] for e in events]
        result = next(e for e in events if e["type"] == "step_result")
        assert result["status"] == "error" and "`code` is missing" in result["result"]

    def test_the_error_tells_the_model_to_answer_in_text_when_the_program_was_long(self, chat_id, home):
        tool = [t for t in make_box(chat_id).tools() if t.name == "run_python"][0]
        try:
            tool.invoke({})
        except Exception as e:
            message = friendly_tool_error("run_python", e)
        assert "put the code in your reply instead" in message

    def test_other_tools_do_not_get_that_advice(self):
        class Fake(Exception):
            def errors(self):
                return [{"loc": ("query",), "type": "missing", "msg": "x"}]
        assert "reply instead" not in friendly_tool_error("search_documents", Fake())


# --- a tool call cut off by the length limit, and saying so once ---

def cut_off_call(name, args_json, call_id="call-1"):
    """A tool-call turn that ended because it ran out of tokens, so its arguments stop mid-way."""
    return [AIMessageChunk(content="", tool_call_chunks=[{"name": name, "args": args_json, "id": call_id, "index": 0}],
                           response_metadata={"done_reason": "length"})]


def every_boost_cut_off(name, args_json):
    """A tool call that stays cut off however much room it is given: the first try and each boosted retry."""
    return [cut_off_call(name, args_json, f"call-{n}") for n in range(1 + llm_prompts.MAX_BOOST)]


class TestCutOffToolCalls:
    def test_a_cut_off_call_is_neither_asked_about_nor_run(self, chat_id, home, monkeypatch):
        ran = []
        monkeypatch.setattr(agent_tools, "run_python_code", lambda code, workspace: ran.append(code) or "1")
        llm = ScriptedLLM(*every_boost_cut_off("run_python", '{"code": "print(1)"}'), text("I will write it in my reply"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "ask"}
        events = list(agent.run_agent(chat_id, "run python to make a backtester", [], config))
        assert ran == [] and "permission_request" not in [e["type"] for e in events]
        result = next(e for e in events if e["type"] == "step_result")
        assert result["status"] == "error" and result["result"] == agent.TOOL_CUT_OFF
        assert "do not retry" in result["result"] and "put the code in your reply" in result["result"]

    def test_a_cut_off_save_is_not_asked_about_either(self, chat_id, home, monkeypatch):
        say(chat_id, ("assistant", REPLY))
        llm = ScriptedLLM(*every_boost_cut_off("save_code", '{"path": "~/Desktop/a.py'), text("sorry"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "ask"}
        events = list(agent.run_agent(chat_id, "save it to my desktop", [], config))
        assert "permission_request" not in [e["type"] for e in events]
        assert not os.path.exists(f"{home}/Desktop/a.py")

    def test_the_model_sees_why_its_call_failed(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(*every_boost_cut_off("run_python", "{}"), text("ok"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "bypass"}
        list(agent.run_agent(chat_id, "run python to make a backtester", [], config))
        assert llm.calls[-1][-1].content == agent.TOOL_CUT_OFF

    def test_a_cut_off_call_followed_by_a_complete_reply_gives_no_notice(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(*every_boost_cut_off("run_python", "{}"), turn_with({"done_reason": "stop"}, "here is the code"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "bypass"}
        events = list(agent.run_agent(chat_id, "run python to make a backtester", [], config))
        assert [e for e in events if e["type"] == "notice"] == []

    def test_two_cut_off_turns_in_one_reply_are_reported_once_for_the_answer_given(self, chat_id, monkeypatch):
        # a turn with no usable output is retried without tools; both hit the limit; the user got only the second
        llm = ScriptedLLM(turn_with({"done_reason": "length"}, ""), *[turn_with({"done_reason": "length"}, f"part {n} ") for n in range(1 + agent.MAX_CONTINUATIONS)])
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        events = list(agent.run_agent(chat_id, "tell me about backtesting", [], chat_manager.get_config(chat_id)))
        assert [e["message"] for e in events if e["type"] == "notice"] == [agent.TRUNCATED_NOTICE]

    def test_a_retry_that_finishes_properly_hides_the_first_turns_cut_off(self, chat_id, monkeypatch):
        llm = ScriptedLLM(turn_with({"done_reason": "length"}, ""), turn_with({"done_reason": "stop"}, "a complete answer"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        events = list(agent.run_agent(chat_id, "tell me about backtesting", [], chat_manager.get_config(chat_id)))
        assert [e for e in events if e["type"] == "notice"] == []

    def test_the_internal_signal_never_reaches_the_browser(self, chat_id, monkeypatch):
        llm = ScriptedLLM(*[turn_with({"done_reason": "length"}, f"part {n} ") for n in range(1 + agent.MAX_CONTINUATIONS)])
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        events = list(agent.run_agent(chat_id, "tell me about backtesting", [], chat_manager.get_config(chat_id)))
        assert "truncated" not in [e["type"] for e in events]


class TestTheOutputLimit:
    def test_replies_may_be_long_enough_for_a_whole_program(self):
        assert llm_prompts.CHAT_MAX_OUTPUT_TOKENS >= 4096

    def test_the_chat_model_is_built_with_that_limit(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(llm_prompts, "get_llm", lambda **kwargs: seen.update(kwargs) or object())
        llm_prompts._chat_llm()
        assert seen["num_predict"] == llm_prompts.CHAT_MAX_OUTPUT_TOKENS

    @pytest.mark.parametrize("boost", range(llm_prompts.MAX_BOOST + 1))
    def test_the_prompt_leaves_room_for_it_in_the_context_window(self, boost):
        limits = llm_prompts.output_limits(boost)
        assert limits["num_ctx"] >= 2 * limits["num_predict"]

    def test_each_boost_doubles_the_room_and_stops_at_the_maximum(self):
        base = llm_prompts.output_limits(0)
        assert llm_prompts.output_limits(1)["num_predict"] == 2 * base["num_predict"]
        assert llm_prompts.output_limits(1)["num_ctx"] == 2 * base["num_ctx"]
        assert llm_prompts.output_limits(99) == llm_prompts.output_limits(llm_prompts.MAX_BOOST)
        assert llm_prompts.output_limits(-1) == base

    def test_a_boosted_model_is_built_with_the_larger_limits(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(llm_prompts, "get_llm", lambda **kwargs: seen.update(kwargs) or object())
        llm_prompts._chat_llm(boost=1)
        assert seen["num_predict"] == 2 * llm_prompts.CHAT_MAX_OUTPUT_TOKENS


# --- what a real small model did on first contact (found by running one, not by guessing) ---

class TestRealModelHabits:
    def test_optional_arguments_passed_as_null_are_fine(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        tool = [t for t in make_box(chat_id).tools() if t.name == "save_code"][0]
        result = tool.invoke({"path": "~/Desktop/a.py", "block": None, "overwrite": None})
        assert result.startswith("saved")
        assert open(f"{home}/Desktop/a.py").read() == BACKTESTER

    def test_a_folder_given_as_the_path_is_refused_not_saved_as_a_file_with_that_name(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        result = make_box(chat_id).save_code(f"{home}/Desktop/athena-save-test")
        assert result.startswith("error:") and "looks like a folder name" in result
        assert f"{home}/Desktop/athena-save-test/backtester.py" in result          # shows what to send instead
        assert os.listdir(f"{home}/Desktop") == []                                  # nothing was written

    def test_a_dotted_folder_name_is_not_mistaken_for_a_file_but_a_real_file_name_is_accepted(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        assert make_box(chat_id).save_code("~/Desktop/Makefile.mk").startswith("saved")
        assert make_box(chat_id).save_code("~/Desktop/x/notes.txt").startswith("saved")

    def test_that_refusal_is_not_put_to_the_user_to_approve(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        assert make_box(chat_id).permission_prompt("save_code", {"path": f"{home}/Desktop/athena-save-test"}) is None

    def test_a_call_that_cannot_run_is_rejected_before_the_user_is_asked(self, chat_id, home, monkeypatch):
        say(chat_id, ("assistant", REPLY))
        llm = ScriptedLLM(call("save_code", {"path": "~/Desktop/a.py", "block": "first"}), text("sorry"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "ask"}
        events = list(agent.run_agent(chat_id, "save it to my desktop", [], config))
        assert "permission_request" not in [e["type"] for e in events]          # the real model was asked first, and then told no
        result = next(e for e in events if e["type"] == "step_result")
        assert result["status"] == "error" and "`block` is not valid" in result["result"]
        assert not os.path.exists(f"{home}/Desktop/a.py")

    def test_the_error_says_to_leave_optional_arguments_out_not_pass_null(self, chat_id, home):
        tool = [t for t in make_box(chat_id).tools() if t.name == "save_code"][0]
        try:
            tool.invoke({"path": "~/Desktop/a.py", "block": "first"})
        except Exception as e:
            message = friendly_tool_error("save_code", e)
        assert "leave optional arguments out rather than passing null" in message

    def test_valid_calls_still_reach_the_user_for_approval(self, chat_id, home, monkeypatch):
        say(chat_id, ("assistant", REPLY))
        llm = ScriptedLLM(call("save_code", {"path": "~/Desktop/a.py", "block": None, "overwrite": None}), text("done"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "ask"}
        events = []
        for event in agent.run_agent(chat_id, "save it to my desktop", [], config):
            events.append(event)
            if event["type"] == "permission_request":
                agent_tools.resolve_permission_request(chat_id, event["id"], True)
        assert any(e["type"] == "permission_request" and e["kind"] == "save" for e in events)
        assert os.path.exists(f"{home}/Desktop/a.py")

    def test_the_descriptions_say_the_path_must_end_in_a_file_name(self, chat_id, home):
        description = {t.name: t.description for t in make_box(chat_id).tools()}["save_code"]
        assert "FILE, ending with its name" in description and "a folder name alone is not enough" in description
        assert "never just the folder" in llm_prompts.tool_hint_lines({"save_code"}, home)

    def test_invalid_arguments_helper_ignores_tools_without_a_schema(self):
        class Bare:
            name = "x"
        assert agent_tools.invalid_arguments(Bare(), {"anything": 1}) is None


class TestTheApprovalSaysWhenAFileIsReplaced:
    def test_a_new_file_is_described_plainly(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        _, shown = make_box(chat_id).permission_prompt("save_code", {"path": "~/Desktop/new.py"})
        assert "REPLACES" not in shown and shown.splitlines()[1].endswith("lines")

    def test_replacing_an_existing_file_is_stated_before_the_user_approves(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        open(f"{home}/Desktop/old.py", "w").write("mine")
        kind, shown = make_box(chat_id).permission_prompt("save_code", {"path": "~/Desktop/old.py", "overwrite": "true"})
        assert kind == "save" and "THIS REPLACES THE FILE THAT IS ALREADY THERE" in shown.splitlines()[1]

    def test_without_overwrite_an_existing_file_is_refused_so_there_is_nothing_to_approve(self, chat_id, home):
        say(chat_id, ("assistant", REPLY))
        open(f"{home}/Desktop/old.py", "w").write("mine")
        assert make_box(chat_id).permission_prompt("save_code", {"path": "~/Desktop/old.py"}) is None


# --- recovering from a reply that ran out of room ---

LENGTH = {"done_reason": "length"}
STOP = {"done_reason": "stop"}


def reply_text(events):
    """What the browser would show: chunks joined, with a reset clearing it."""
    shown = ""
    for event in events:
        if event["type"] == "reset":
            shown = ""
        elif event["type"] == "chunk":
            shown += event["content"]
    return shown


def run_loop(chat_id, monkeypatch, llm, question="tell me about backtesting", built=None):
    def build(boost=0):
        if built is not None:
            built.append(boost)
        return llm

    monkeypatch.setattr(agent, "_chat_llm", build)
    return list(agent.run_agent(chat_id, question, [], chat_manager.get_config(chat_id)))


class TestContinuingACutOffReply:
    def test_a_cut_off_reply_is_continued_and_arrives_whole(self, chat_id, monkeypatch):
        llm = ScriptedLLM(turn_with(LENGTH, "The first half of the answer, "), turn_with(STOP, "and the second half."))
        events = run_loop(chat_id, monkeypatch, llm)
        assert reply_text(events) == "The first half of the answer, and the second half."
        assert [e for e in events if e["type"] == "notice"] == []

    def test_the_model_is_asked_to_carry_on_with_its_own_text_in_front_of_it(self, chat_id, monkeypatch):
        llm = ScriptedLLM(turn_with(LENGTH, "first part "), turn_with(STOP, "second part"))
        run_loop(chat_id, monkeypatch, llm)
        asked = llm.calls[1]
        assert asked[-2].content == "first part " and asked[-1].content == agent.CONTINUE_PROMPT

    def test_the_context_remembers_the_whole_answer_not_one_piece(self, chat_id, monkeypatch):
        llm = ScriptedLLM(turn_with(LENGTH, "one, "), turn_with(LENGTH, "two, "), turn_with(STOP, "three"))
        run_loop(chat_id, monkeypatch, llm)
        assert [e["answer"] for e in chat_manager.get_context(chat_id) if e["kind"] == "turn"] == ["one, two, three"]

    def test_each_round_carries_the_answer_so_far_not_a_pile_of_old_attempts(self, chat_id, monkeypatch):
        llm = ScriptedLLM(turn_with(LENGTH, "one "), turn_with(LENGTH, "two "), turn_with(STOP, "three"))
        run_loop(chat_id, monkeypatch, llm)
        assert llm.calls[2][-2].content == "one two " and len(llm.calls[2]) == len(llm.calls[1])

    def test_it_gives_up_after_the_limit_and_says_so_once(self, chat_id, monkeypatch):
        llm = ScriptedLLM(*[turn_with(LENGTH, f"part {n} ") for n in range(10)])
        events = run_loop(chat_id, monkeypatch, llm)
        assert len(llm.calls) == 1 + agent.MAX_CONTINUATIONS
        assert [e["message"] for e in events if e["type"] == "notice"] == [agent.TRUNCATED_NOTICE]

    def test_a_reply_that_finished_is_never_continued(self, chat_id, monkeypatch):
        llm = ScriptedLLM(turn_with(STOP, "all of it"))
        run_loop(chat_id, monkeypatch, llm)
        assert len(llm.calls) == 1

    def test_a_continuation_that_repeats_the_end_of_what_was_written_does_not_say_it_twice(self, chat_id, monkeypatch):
        llm = ScriptedLLM(turn_with(LENGTH, "def total(items):\n    return sum(i.price for i in items)"),
                          turn_with(STOP, "return sum(i.price for i in items)\n\nprint(total(cart))"))
        events = run_loop(chat_id, monkeypatch, llm, question="write python code for a cart total")
        assert reply_text(events) == "def total(items):\n    return sum(i.price for i in items)\n\nprint(total(cart))"

    def test_the_room_for_the_next_round_grows_as_the_answer_does(self, chat_id, monkeypatch):
        built = []
        llm = ScriptedLLM(turn_with(LENGTH, "a "), turn_with(LENGTH, "b "), turn_with(STOP, "c"))
        run_loop(chat_id, monkeypatch, llm, built=built)
        assert built == [0, 1, 2]

    def test_nothing_is_continued_when_nothing_was_written(self, chat_id, monkeypatch):
        # an empty cut-off turn is the existing "plain sentences" retry's job, not a continuation
        llm = ScriptedLLM(turn_with(LENGTH, ""), turn_with(STOP, "the answer"))
        events = run_loop(chat_id, monkeypatch, llm)
        assert reply_text(events) == "the answer"
        assert agent.CONTINUE_PROMPT not in [m.content for call in llm.calls for m in call]


class TestRetryingACutOffToolCall:
    def test_a_tool_call_that_ran_out_of_room_is_asked_for_again_with_more(self, chat_id, home, monkeypatch):
        ran = []
        monkeypatch.setattr(agent_tools, "run_python_code", lambda code, workspace: ran.append(code) or "1")
        built = []
        llm = ScriptedLLM(cut_off_call("run_python", '{"code": "print('), call("run_python", {"code": "print(1)"}), text("it printed 1"))
        config = {**chat_manager.get_config(chat_id), "permission_mode": "bypass"}
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: built.append(boost) or llm)
        events = list(agent.run_agent(chat_id, "run python to print 1", [], config))
        assert ran == ["print(1)"] and built == [0, 1]
        steps = [e for e in events if e["type"] == "step"]
        assert len(steps) == 1                      # the cut-off attempt was never shown as a step
        assert reply_text(events) == "it printed 1"

    def test_the_half_written_attempt_is_not_left_in_the_conversation(self, chat_id, home, monkeypatch):
        llm = ScriptedLLM(cut_off_call("run_python", '{"code": "print('), text("never mind"))
        monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
        config = {**chat_manager.get_config(chat_id), "permission_mode": "bypass"}
        list(agent.run_agent(chat_id, "run python to print 1", [], config))
        assert not any(getattr(m, "tool_calls", None) for m in llm.calls[1])

    def test_text_written_before_the_cut_off_call_is_cleared(self, chat_id, home, monkeypatch):
        thought = [AIMessageChunk(content="let me write that")] + cut_off_call("run_python", '{"code": "x')
        llm = ScriptedLLM(thought, text("plain answer"))
        events = run_loop(chat_id, monkeypatch, llm, question="run python to print 1")
        assert reply_text(events) == "plain answer"


class TestStitchingAContinuation:
    def test_a_repeat_of_the_end_is_dropped(self):
        assert agent.trim_overlap("the quick brown fox jumps", "brown fox jumps over the dog") == " over the dog"

    def test_a_repeat_shorter_than_a_dozen_characters_is_left_alone(self):
        assert agent.trim_overlap("and so on", "on and on") == "on and on"

    def test_no_repeat_means_no_change(self):
        assert agent.trim_overlap("one two three", "four five six seven") == "four five six seven"

    def test_only_the_end_of_what_was_written_counts(self):
        assert agent.trim_overlap("x" * 1000 + "tail of the text", "tail of the text, then more") == ", then more"

    def test_the_stitcher_holds_the_start_back_then_streams_straight_through(self):
        stitcher = agent.Stitcher("ends with: hello wonderful world")
        out = stitcher.feed("hello wonder")
        assert out == ""                                    # not yet known whether this repeats
        out += stitcher.feed("ful world and then " + "z" * 200)
        assert out.startswith(" and then ") and "hello" not in out
        assert stitcher.feed("more") == "more"

    def test_a_short_continuation_is_released_when_it_ends(self):
        stitcher = agent.Stitcher("a long enough tail to compare against")
        assert stitcher.feed("short") == ""
        assert stitcher.flush() == "short"
