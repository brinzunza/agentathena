"""
The exact words a model is given - its instructions, the tools it can call and how they are described - are
committed in tests/snapshots/prompt_surface.txt, and this test fails when they change.

They look like code but behave like configuration: the prompts here were tuned against small local models, and
one reworded line can quietly make an 8B model start calling tools for "hello". Nothing else would notice. A
change to this surface shows up as a diff in review, where someone can ask whether it was meant.

After a deliberate change, regenerate the file and commit it with the change:

    UPDATE_PROMPT_SNAPSHOT=1 python3 -m pytest tests/test_prompt_snapshot.py
"""

import difflib
import json
import os

import pytest
from langchain_core.messages import AIMessageChunk

import agent
import agent_tools
import chat_manager
import llm_prompts
from fakes import ScriptedLLM

SNAPSHOT = os.path.join(os.path.dirname(__file__), "snapshots", "prompt_surface.txt")
TODAY = "Monday, November 16, 2026"
HOME = "/home/athena"
UPDATE_HINT = "If this change is deliberate, run: UPDATE_PROMPT_SNAPSHOT=1 python3 -m pytest tests/test_prompt_snapshot.py"


def templates(prompt):
    """The text of each message in a ChatPromptTemplate."""
    return [f"[{message.__class__.__name__.replace('MessagePromptTemplate', '').lower()}]\n{message.prompt.template}"
            for message in prompt.messages]


def tool_schemas(box):
    out = []
    for tool in box.tools():
        schema = tool.get_input_schema().model_json_schema()
        out.append(f"## {tool.name}\n{tool.description}\narguments: {json.dumps(schema.get('properties', {}), sort_keys=True)}\n"
                   f"required: {schema.get('required', [])}")
    return "\n\n".join(out)


def prompt_as_sent(chat_id, monkeypatch, question, history=(), config=None):
    """The system prompt run_agent really builds for a message, captured at the model."""
    llm = ScriptedLLM([AIMessageChunk(content="ok", response_metadata={"done_reason": "stop"})])
    monkeypatch.setattr(agent, "_chat_llm", lambda boost=0: llm)
    monkeypatch.setattr(agent, "today_string", lambda: TODAY)
    monkeypatch.setattr(agent, "real_home", lambda: HOME)
    list(agent.run_agent(chat_id, question, list(history), config or chat_manager.get_config(chat_id)))
    return llm.calls[0][0].content


def render(chat_id, monkeypatch):
    monkeypatch.setattr(agent_tools, "real_home", lambda: HOME)
    box = agent_tools.ToolBox(chat_id, chat_manager.get_config(chat_id))
    sections = [
        ("agent system prompt (template)", llm_prompts.agent_system_prompt),
        ("tool guidance, every tool", llm_prompts.tool_hint_lines(set(llm_prompts.agent_tool_hints), HOME)),
        ("tools as the model receives them", tool_schemas(box)),
        ("classic answer prompt", "\n\n".join(templates(llm_prompts.generator_prompt))),
        ("relevance check prompt", "\n\n".join(templates(llm_prompts.relevance_prompt))),
        ("hallucination grader prompt", "\n\n".join(templates(llm_prompts.hallucination_prompt))),
        ("answer grader prompt", "\n\n".join(templates(llm_prompts.answer_prompt))),
        ("question rewrite prompt", "\n\n".join(templates(llm_prompts.rewrite_prompt))),
        ("context compaction prompt", "\n\n".join(templates(llm_prompts.compact_prompt))),
        ("chat title prompt", "\n\n".join(templates(llm_prompts.title_prompt))),
        ("topic match prompt", "\n\n".join(templates(llm_prompts.topic_match_prompt))),
        ("research plan prompt", "\n\n".join(templates(llm_prompts.research_plan_prompt))),
        ("research note prompt", llm_prompts.research_note_prompt),
        ("research report prompt", "\n\n".join(templates(llm_prompts.research_report_prompt))),
        ("output limits", f"base {llm_prompts.output_limits(0)}\nboosted {llm_prompts.output_limits(1)}"),
    ]
    return "\n\n".join(f"===== {title} =====\n{body.strip()}" for title, body in sections) + "\n"


def render_assembled(chat_id, monkeypatch):
    """The system prompt as assembled for a few kinds of message: it is the order that matters here."""
    monkeypatch.setattr(agent_tools.ToolBox, "document_names", lambda self: [])
    plain = prompt_as_sent(chat_id, monkeypatch, "hello")
    monkeypatch.setattr(agent_tools.ToolBox, "document_names", lambda self: ["hw3.pdf", "notes.md"])
    with_docs = prompt_as_sent(chat_id, monkeypatch, "when is homework 3 due?")
    asks_for_code = prompt_as_sent(chat_id, monkeypatch, "write python code that sums a list")
    facts = prompt_as_sent(chat_id, monkeypatch, "what is my name?", history=[("my name is Ada", "Nice to meet you, Ada.")])
    return "\n\n".join(f"===== assembled: {title} =====\n{body}" for title, body in (
        ("a greeting, no documents", plain), ("a question about documents", with_docs),
        ("a request for code", asks_for_code), ("after the user stated a fact", facts)))


def current(chat_id, monkeypatch):
    return render(chat_id, monkeypatch) + "\n" + render_assembled(chat_id, monkeypatch) + "\n"


def test_the_prompts_and_tools_are_what_was_reviewed(chat_id, monkeypatch):
    actual = current(chat_id, monkeypatch)
    if os.environ.get("UPDATE_PROMPT_SNAPSHOT"):
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        with open(SNAPSHOT, "w", encoding="utf-8") as f:
            f.write(actual)
        pytest.skip("snapshot rewritten")
    assert os.path.exists(SNAPSHOT), f"no snapshot yet. {UPDATE_HINT}"
    expected = open(SNAPSHOT, encoding="utf-8").read()
    if actual != expected:
        diff = "".join(list(difflib.unified_diff(expected.splitlines(True), actual.splitlines(True),
                                                 "reviewed", "now", n=1))[:80])
        pytest.fail(f"the prompts or tools the model sees have changed:\n{diff}\n{UPDATE_HINT}")


class TestThePromptKeepsItsShape:
    """What matters about the order, so a harmless-looking edit can't undo it (see the measurement in README)."""

    def test_everything_that_varies_comes_after_everything_that_does_not(self, chat_id, monkeypatch):
        monkeypatch.setattr(agent_tools.ToolBox, "document_names", lambda self: [])
        one = prompt_as_sent(chat_id, monkeypatch, "hello")
        monkeypatch.setattr(agent_tools.ToolBox, "document_names", lambda self: ["hw3.pdf"])
        two = prompt_as_sent(chat_id, monkeypatch, "write python code that sums a list")
        common = os.path.commonprefix([one, two])
        # The whole rulebook is shared; only "Right now" and what follows may differ
        assert len(common) >= len(llm_prompts.agent_system_prompt.split("Right now:")[0])

    def test_the_date_and_documents_and_tools_are_all_after_the_rules(self):
        rules, _, rest = llm_prompts.agent_system_prompt.partition("Right now:")
        assert "{today}" not in rules and "{tools}" not in rules and "{knowledge}" not in rules
        assert all(token in rest for token in ("{today}", "{knowledge}", "{tools}"))

    def test_no_placeholder_is_left_in_a_built_prompt(self):
        built = llm_prompts.build_agent_system_prompt("Tools:\n- x", TODAY, "no documents", ["my name is Ada"])
        assert "{" not in built.replace("{home}", "") and TODAY in built and "my name is Ada" in built
