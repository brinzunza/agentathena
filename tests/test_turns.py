import json

import pytest

import chat_manager
import turns
from turns import TurnRecorder


def saved(chat_id):
    return chat_manager.get_messages(chat_id)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TestBeginning:
    def test_the_question_and_an_empty_streaming_reply_are_saved_at_once(self, chat_id):
        index = TurnRecorder(chat_id, "what is 2+2?").begin()
        messages = saved(chat_id)
        assert index == 1
        assert messages[0] == {"role": "user", "content": "what is 2+2?"}
        assert messages[1]["role"] == "assistant" and messages[1]["streaming"] is True

    def test_it_appends_to_what_the_chat_already_holds(self, chat_id):
        chat_manager.save_messages(chat_id, [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
        assert TurnRecorder(chat_id, "c").begin() == 3
        assert [m["content"] for m in saved(chat_id)] == ["a", "b", "c", ""]


class TestTheReplyAsItStreams:
    def start(self, chat_id, clock=None):
        recorder = TurnRecorder(chat_id, "q", clock=clock or Clock())
        recorder.begin()
        return recorder

    def test_chunks_are_held_back_until_enough_time_has_passed(self, chat_id):
        clock = Clock()
        recorder = self.start(chat_id, clock)
        recorder.apply({"type": "chunk", "content": "hel"})
        assert saved(chat_id)[1]["content"] == ""              # not written for every chunk
        clock.now += turns.CHUNK_SAVE_SECONDS
        recorder.apply({"type": "chunk", "content": "lo"})
        assert saved(chat_id)[1]["content"] == "hello"

    def test_a_reset_drops_what_the_agent_said_before_using_a_tool(self, chat_id):
        recorder = self.start(chat_id)
        recorder.apply({"type": "chunk", "content": "let me look"})
        recorder.apply({"type": "reset"})
        assert saved(chat_id)[1]["content"] == ""

    def test_steps_are_saved_as_they_start_and_finish(self, chat_id):
        recorder = self.start(chat_id)
        recorder.apply({"type": "step", "id": 1, "tool": "calculator", "args": {"expression": "2+2"}, "thought": ""})
        assert saved(chat_id)[1]["steps"][0]["status"] == "running"
        recorder.apply({"type": "step_result", "id": 1, "status": "ok", "result": "4"})
        step = saved(chat_id)[1]["steps"][0]
        assert (step["status"], step["result"], step["args"]) == ("ok", "4", {"expression": "2+2"})

    def test_a_permission_request_is_kept_on_its_step_and_expires_with_the_result(self, chat_id):
        recorder = self.start(chat_id)
        recorder.apply({"type": "step", "id": 1, "tool": "run_python", "args": {}, "thought": ""})
        recorder.apply({"type": "permission_request", "id": "p1", "step": 1, "tool": "run_python", "code": "x=1", "kind": "code"})
        assert saved(chat_id)[1]["steps"][0]["permission"] == {
            "id": "p1", "code": "x=1", "kind": "code", "state": "pending", "can_remember": True}
        recorder.apply({"type": "step_result", "id": 1, "status": "error", "result": "error: timed out"})
        assert saved(chat_id)[1]["steps"][0]["permission"]["state"] == "expired"

    @pytest.mark.parametrize("state", ["approved", "denied", "expired"])
    def test_what_the_user_answered_is_kept_so_a_reload_still_says_it(self, chat_id, state):
        recorder = self.start(chat_id)
        recorder.apply({"type": "step", "id": 1, "tool": "run_python", "args": {}, "thought": ""})
        recorder.apply({"type": "permission_request", "id": "p1", "step": 1, "tool": "run_python", "code": "x", "kind": "code"})
        recorder.apply({"type": "permission_decided", "id": "p1", "step": 1, "state": state})
        recorder.apply({"type": "step_result", "id": 1, "status": "ok", "result": "1"})
        assert saved(chat_id)[1]["steps"][0]["permission"]["state"] == state       # the result doesn't turn it into "expired"

    def test_an_answer_for_another_request_is_ignored(self, chat_id):
        recorder = self.start(chat_id)
        recorder.apply({"type": "step", "id": 1, "tool": "run_python", "args": {}, "thought": ""})
        recorder.apply({"type": "permission_request", "id": "p1", "step": 1, "tool": "run_python", "code": "x", "kind": "code"})
        recorder.apply({"type": "permission_decided", "id": "other", "step": 1, "state": "approved"})
        assert saved(chat_id)[1]["steps"][0]["permission"]["state"] == "pending"

    def test_how_a_step_came_to_run_without_asking_is_kept(self, chat_id):
        recorder = self.start(chat_id)
        recorder.apply({"type": "step", "id": 1, "tool": "run_python", "args": {}, "thought": ""})
        recorder.apply({"type": "step_result", "id": 1, "status": "ok", "result": "1", "approval": "chat"})
        assert saved(chat_id)[1]["steps"][0]["approval"] == "chat"

    def test_notices_are_joined_without_repeating(self, chat_id):
        recorder = self.start(chat_id)
        recorder.apply({"type": "notice", "message": "one"})
        recorder.apply({"type": "notice", "message": "one"})
        recorder.apply({"type": "notice", "message": "two"})
        assert saved(chat_id)[1]["notice"] == "one two"


class TestHowATurnEnds:
    def run(self, chat_id):
        recorder = TurnRecorder(chat_id, "q", clock=Clock())
        recorder.begin()
        recorder.apply({"type": "chunk", "content": "the answer"})
        return recorder

    def test_complete_settles_the_reply_with_its_time_and_sources(self, chat_id):
        recorder = self.run(chat_id)
        recorder.apply({"type": "sources", "content": [{"file_name": "a.md"}]})
        recorder.apply({"type": "complete", "response_time": 1.5})
        reply = saved(chat_id)[1]
        assert reply["content"] == "the answer" and reply["response_time"] == 1.5
        assert "streaming" not in reply and reply["sources"] == [{"file_name": "a.md"}]

    def test_an_error_replaces_the_reply_with_the_error_as_the_browser_shows_it(self, chat_id):
        recorder = self.run(chat_id)
        recorder.fail("can't reach Ollama", "ollama_unreachable")
        assert saved(chat_id)[1] == {"role": "error", "content": "can't reach Ollama", "code": "ollama_unreachable"}

    def test_the_browser_leaving_keeps_what_was_written_and_says_so(self, chat_id):
        recorder = self.run(chat_id)
        recorder.apply({"type": "step", "id": 1, "tool": "web_search", "args": {}, "thought": ""})
        recorder.interrupted()
        reply = saved(chat_id)[1]
        assert reply["content"] == "the answer" and "streaming" not in reply
        assert reply["steps"][0]["status"] == "stopped"                    # not left "running" forever
        assert reply["notice"] == turns.INTERRUPTED_NOTICE

    def test_leaving_before_anything_was_written_adds_no_notice(self, chat_id):
        recorder = TurnRecorder(chat_id, "q")
        recorder.begin()
        recorder.interrupted()
        assert "notice" not in saved(chat_id)[1]

    def test_nothing_changes_after_the_turn_has_ended(self, chat_id):
        recorder = self.run(chat_id)
        recorder.apply({"type": "complete", "response_time": 1})
        recorder.interrupted()                       # the finally block of the stream
        recorder.apply({"type": "chunk", "content": "late"})
        assert saved(chat_id)[1]["content"] == "the answer" and "notice" not in saved(chat_id)[1]

    def test_a_chat_cleared_mid_reply_is_not_refilled(self, chat_id):
        recorder = self.run(chat_id)
        chat_manager.save_messages(chat_id, [])
        recorder.apply({"type": "complete", "response_time": 1})
        assert saved(chat_id) == []

    def test_a_reply_that_moved_is_still_found(self, chat_id):
        recorder = self.run(chat_id)
        chat_manager.update_messages(chat_id, lambda m: [{"role": "system", "content": "note"}, *m])
        recorder.apply({"type": "complete", "response_time": 1})
        assert [m["role"] for m in saved(chat_id)] == ["system", "user", "assistant"]
        assert saved(chat_id)[2]["content"] == "the answer"


class TestAfterARestart:
    def test_a_reply_left_streaming_is_settled(self, chat_id):
        recorder = TurnRecorder(chat_id, "q")
        recorder.begin()
        recorder.apply({"type": "step", "id": 1, "tool": "web_search", "args": {}, "thought": ""})
        assert turns.settle_interrupted_replies() == 1
        reply = saved(chat_id)[1]
        assert "streaming" not in reply and reply["steps"][0]["status"] == "stopped"
        assert reply["notice"] == turns.RESTART_NOTICE

    def test_chats_with_nothing_to_settle_are_left_alone(self, chat_id):
        chat_manager.save_messages(chat_id, [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
        assert turns.settle_interrupted_replies() == 0


class TestTheStreamEndpoint:
    @pytest.fixture
    def client(self, monkeypatch):
        import main
        from fastapi.testclient import TestClient
        return main, TestClient(main.app)

    def stream(self, client, chat_id):
        response = client.post(f"/chats/{chat_id}/qa/chat/stream", json={"content": "hi there"})
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def test_the_server_saves_the_conversation_as_it_streams(self, client, chat_id, monkeypatch):
        main, http = client

        def agent(chat, question, history, config):
            yield {"type": "chunk", "content": "hello"}
            yield {"type": "complete", "response_time": 0.5, "steps": 0}

        monkeypatch.setattr(main, "run_agent", agent)
        events = self.stream(http, chat_id)
        assert events[0] == {"type": "started"}
        messages = saved(chat_id)
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["content"] == "hello" and "streaming" not in messages[1]

    def test_a_failing_model_leaves_the_error_in_the_saved_chat(self, client, chat_id, monkeypatch):
        main, http = client

        def agent(chat, question, history, config):
            raise RuntimeError("boom")
            yield

        monkeypatch.setattr(main, "run_agent", agent)
        events = self.stream(http, chat_id)
        assert events[-1]["type"] == "error"
        assert [m["role"] for m in saved(chat_id)] == ["user", "error"]

    def test_a_stream_dropped_midway_still_saves_the_partial_reply(self, client, chat_id, monkeypatch):
        main, http = client

        def agent(chat, question, history, config):
            yield {"type": "chunk", "content": "partial"}
            yield {"type": "chunk", "content": " more"}
            yield {"type": "complete", "response_time": 1, "steps": 0}

        monkeypatch.setattr(main, "run_agent", agent)
        # Read two events, then close the generator the way a dropped connection does
        response = http.post(f"/chats/{chat_id}/qa/chat/stream", json={"content": "q"})
        assert response.status_code == 200
        # (the full run is covered above; here, drive the endpoint's generator directly)
        import asyncio

        async def drive():
            streaming = await main.qa_chat_stream(chat_id, main.ChatMessage(content="again"))
            it = streaming.body_iterator
            first = await it.__anext__()        # "started"
            second = await it.__anext__()       # the first chunk
            await it.aclose()
            return first, second

        asyncio.run(drive())
        last = [m for m in saved(chat_id) if m["role"] == "assistant"][-1]
        assert "streaming" not in last


class TestADroppedConnection:
    """Closing the stream at any point must leave no reply marked as still being written."""

    @pytest.fixture
    def stream(self, chat_id, monkeypatch):
        import asyncio
        import main

        def agent(chat, question, history, config):
            yield {"type": "chunk", "content": "one"}
            yield {"type": "chunk", "content": " two"}
            yield {"type": "complete", "response_time": 1, "steps": 0}

        monkeypatch.setattr(main, "run_agent", agent)

        def read_then_drop(events_to_read):
            async def drive():
                streaming = await main.qa_chat_stream(chat_id, main.ChatMessage(content="hi"))
                iterator = streaming.body_iterator
                for _ in range(events_to_read):
                    await iterator.__anext__()
                await iterator.aclose()

            asyncio.run(drive())
            return saved(chat_id)

        return read_then_drop

    def test_dropped_straight_after_started(self, stream):
        # The case a real browser hit: the very first event was all that was read
        messages = stream(1)
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert "streaming" not in messages[1]

    def test_dropped_after_some_text(self, stream):
        messages = stream(2)
        assert "streaming" not in messages[1] and messages[1]["content"] == "one"
        assert messages[1]["notice"] == turns.INTERRUPTED_NOTICE

    def test_dropped_before_anything_was_written_leaves_no_notice_and_no_streaming_flag(self, stream):
        messages = stream(1)
        assert "notice" not in messages[1] and "streaming" not in messages[1]


class TestWhenTheGeneratorIsNotClosed:
    """Starlette doesn't close the generator when the browser leaves: the response has to settle the reply itself."""

    def test_the_response_settles_a_reply_whose_stream_was_simply_abandoned(self, chat_id, monkeypatch):
        import asyncio
        import main

        def agent(chat, question, history, config):
            yield {"type": "chunk", "content": "partial"}
            yield {"type": "chunk", "content": " more"}
            yield {"type": "complete", "response_time": 1, "steps": 0}

        monkeypatch.setattr(main, "run_agent", agent)

        async def drive():
            streaming = await main.qa_chat_stream(chat_id, main.ChatMessage(content="hi"))
            iterator = streaming.body_iterator
            await iterator.__anext__()                 # started
            await iterator.__anext__()                 # partial
            assert saved(chat_id)[1].get("streaming")  # still being written ...
            await streaming.background()               # ... until the response is over (the browser left, nothing closed the stream)

        asyncio.run(drive())
        reply = saved(chat_id)[1]
        assert "streaming" not in reply and reply["notice"] == turns.INTERRUPTED_NOTICE

    def test_a_reply_that_finished_is_not_marked_interrupted_by_the_same_call(self, chat_id, monkeypatch):
        import asyncio
        import main

        monkeypatch.setattr(main, "run_agent", lambda *a: iter([{"type": "chunk", "content": "whole"},
                                                                 {"type": "complete", "response_time": 1, "steps": 0}]))

        async def drive():
            streaming = await main.qa_chat_stream(chat_id, main.ChatMessage(content="hi"))
            async for _ in streaming.body_iterator:
                pass
            await streaming.background()

        asyncio.run(drive())
        assert "notice" not in saved(chat_id)[1] and saved(chat_id)[1]["content"] == "whole"

    def test_events_that_arrive_after_the_reply_was_settled_change_nothing(self, chat_id):
        recorder = TurnRecorder(chat_id, "q")
        recorder.begin()
        recorder.apply({"type": "chunk", "content": "a"})
        recorder.interrupted()
        recorder.apply({"type": "chunk", "content": "b"})
        recorder.apply({"type": "complete", "response_time": 1})
        assert saved(chat_id)[1]["content"] == "a" and "response_time" not in saved(chat_id)[1] or saved(chat_id)[1].get("response_time") is None
