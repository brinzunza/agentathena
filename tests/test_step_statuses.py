"""The server and the browser each have a list of the ways a tool call can end; this keeps them in step."""

import os
import re

import agent

STEPS_JS = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "steps.js")


def browser_statuses():
    source = open(STEPS_JS, encoding="utf-8").read()
    table = source.split("export const STEP_STATUS = {", 1)[1].split("};", 1)[0]
    return set(re.findall(r"^\s{2}(\w+):\s*\{", table, flags=re.M))


def test_the_browser_knows_every_status_the_server_can_send():
    missing = set(agent.STEP_STATUSES) - browser_statuses()
    assert not missing, f"frontend/src/steps.js has no entry for: {sorted(missing)}"


def test_the_browser_only_adds_the_two_states_only_it_can_see():
    assert browser_statuses() - set(agent.STEP_STATUSES) == {"running", "stopped"}


def test_the_agent_loop_only_uses_the_declared_statuses(monkeypatch):
    source = open(agent.__file__, encoding="utf-8").read()
    assert not re.findall(r'status\s*=\s*"(?:ok|error|denied|skipped)"|"status":\s*"(?:ok|error|denied|skipped)"', source)
