import json
import threading

import pytest

import chat_manager
import titles
from titles import clean_title, heuristic_title


class TestCleaningATitle:
    @pytest.mark.parametrize("raw, clean", [
        ('"Quarterly sales figures"', "Quarterly sales figures"),
        ("Title: Quarterly sales figures.", "Quarterly sales figures"),
        ("**Quarterly sales**", "Quarterly sales"),
        ("  Quarterly sales  \nA second line the model added", "Quarterly sales"),
        ("“Smart quotes”", "Smart quotes"),
        ("one two three four five six seven eight", "one two three four five six"),
        ("", ""), (None, ""), ('""', ""), ("...", ""),
    ])
    def test_it_is_made_fit_to_be_a_title(self, raw, clean):
        assert clean_title(raw) == clean

    def test_it_is_cut_to_a_length_that_fits_the_sidebar(self):
        assert len(clean_title("a" * 300)) <= titles.MAX_CHARS


class TestWithoutTheModel:
    @pytest.mark.parametrize("question, title", [
        ("When is homework 3 due?", "When is homework 3 due"),
        ("Can you please summarize the lecture notes on thermodynamics and entropy", "Summarize the lecture notes on thermodynamics"),
        ("hi, write a python script that sums a list", "Write a python script that sums"),
        ("", ""), ("   ", ""),
    ])
    def test_the_first_words_of_the_question_make_a_title(self, question, title):
        assert heuristic_title(question) == title


class Writer:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.seen = reply, error, []

    def invoke(self, values):
        self.seen.append(values)
        if self.error:
            raise self.error
        return self.reply


class TestGeneratingOne:
    def test_the_models_title_is_used(self):
        assert titles.generate_title("q", "a", Writer('"Sales report"')) == "Sales report"

    def test_the_model_reads_the_question_and_the_start_of_the_answer(self):
        writer = Writer("T")
        titles.generate_title("q" * 2000, "a" * 5000, writer)
        assert len(writer.seen[0]["question"]) <= 600 and len(writer.seen[0]["answer"]) <= titles.ANSWER_CHARS

    def test_a_failing_model_falls_back_to_the_question(self):
        assert titles.generate_title("When is homework 3 due?", "a", Writer(error=RuntimeError("down"))) == "When is homework 3 due"

    def test_an_empty_reply_falls_back_to_the_question(self):
        assert titles.generate_title("Plan a trip to Lisbon", "a", Writer("")) == "Plan a trip to Lisbon"

    def test_a_title_that_is_just_the_placeholder_is_not_a_title(self):
        assert titles.generate_title("Plan a trip to Lisbon", "a", Writer("New chat")) == "Plan a trip to Lisbon"


class TestNamingTheChat:
    def test_a_chat_started_without_a_name_says_so(self):
        chat = chat_manager.create_chat("")
        assert chat["name"] == "new chat" and chat["untitled"] is True
        assert chat_manager.create_chat("Named")["untitled"] is False
        assert chat_manager.create_chat("   ")["untitled"] is True

    def test_the_first_exchange_names_it(self):
        chat = chat_manager.create_chat("")["id"]
        assert titles.title_chat(chat, "q", "a", Writer("Sales report")) == "Sales report"
        meta = chat_manager.get_meta(chat)
        assert meta["name"] == "Sales report" and meta["untitled"] is False

    def test_a_chat_that_has_a_name_is_left_alone(self):
        chat = chat_manager.create_chat("Mine")["id"]
        writer = Writer("Other")
        assert titles.title_chat(chat, "q", "a", writer) is None
        assert chat_manager.get_meta(chat)["name"] == "Mine" and writer.seen == []

    def test_a_chat_is_titled_once(self):
        chat = chat_manager.create_chat("")["id"]
        titles.title_chat(chat, "q", "a", Writer("First"))
        assert titles.title_chat(chat, "q2", "a2", Writer("Second")) is None
        assert chat_manager.get_meta(chat)["name"] == "First"

    def test_a_name_typed_while_the_model_was_thinking_wins(self):
        chat = chat_manager.create_chat("")["id"]

        class Slow(Writer):
            def invoke(self, values):
                chat_manager.rename_chat(chat, "My own name")       # the user got there first
                return "Generated"

        assert titles.title_chat(chat, "q", "a", Slow()) is None
        assert chat_manager.get_meta(chat)["name"] == "My own name"

    def test_renaming_ends_the_placeholder(self):
        chat = chat_manager.create_chat("")["id"]
        chat_manager.rename_chat(chat, "Mine")
        assert chat_manager.get_meta(chat)["untitled"] is False

    def test_a_deleted_chat_is_not_an_error(self):
        chat = chat_manager.create_chat("")["id"]
        chat_manager.delete_chat(chat)
        assert titles.title_chat(chat, "q", "a", Writer("x")) is None

    def test_the_title_fits_the_name_limit(self):
        chat = chat_manager.create_chat("")["id"]
        chat_manager.set_generated_title(chat, "x" * 500)
        assert len(chat_manager.get_meta(chat)["name"]) <= chat_manager.MAX_CHAT_NAME_CHARS

    def test_the_chat_list_says_which_are_untitled(self):
        chat_manager.create_chat("")
        chat_manager.create_chat("Named")
        assert sorted(c["untitled"] for c in chat_manager.list_chats()) == [False, True]


class TestFromTheStream:
    @pytest.fixture
    def http(self):
        import main
        from fastapi.testclient import TestClient
        return main, TestClient(main.app)

    def test_a_finished_reply_triggers_titling_of_an_untitled_chat(self, http, monkeypatch):
        main, client = http
        chat = client.post("/chats", json={}).json()
        assert chat["name"] == "new chat" and chat["untitled"] is True
        monkeypatch.setattr(main, "run_agent", lambda *a: iter([
            {"type": "chunk", "content": "Because."}, {"type": "complete", "response_time": 0, "steps": 0}]))
        called = []
        monkeypatch.setattr(main.titles, "schedule", lambda *args: called.append(args))
        client.post(f"/chats/{chat['id']}/qa/chat/stream", json={"content": "why is the sky blue?"})
        assert called == [(chat["id"], "why is the sky blue?", "Because.")]

    def test_a_failed_reply_does_not(self, http, monkeypatch):
        main, client = http
        chat = client.post("/chats", json={}).json()

        def boom(*a):
            raise RuntimeError("down")
            yield

        monkeypatch.setattr(main, "run_agent", boom)
        called = []
        monkeypatch.setattr(main.titles, "schedule", lambda *args: called.append(args))
        client.post(f"/chats/{chat['id']}/qa/chat/stream", json={"content": "hi"})
        assert called == []

    def test_scheduling_names_the_chat_in_the_background(self, monkeypatch):
        import llm_prompts
        monkeypatch.setattr(llm_prompts, "get_title_writer", lambda: Writer(error=RuntimeError("no model in tests")))
        chat = chat_manager.create_chat("")["id"]
        thread = titles.schedule(chat, "When is homework 3 due?", "Friday.")
        thread.join(timeout=30)
        assert chat_manager.get_meta(chat)["untitled"] is False       # the model failed, so the question is used
        assert chat_manager.get_meta(chat)["name"] == "When is homework 3 due"

    def test_scheduling_for_a_named_chat_does_nothing(self):
        chat = chat_manager.create_chat("Named")["id"]
        assert titles.schedule(chat, "q", "a") is None
