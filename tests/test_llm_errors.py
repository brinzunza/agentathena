import pytest
from langchain_core.messages import AIMessageChunk

import agent
import chat_manager
import llm_config
import llm_errors
from llm_errors import classify
from fakes import ScriptedLLM, text


class WithStatus(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status_code = status


class TestWhatWentWrong:
    @pytest.mark.parametrize("error, code", [
        (RuntimeError("insufficient_quota: you exceeded"), "no_credits"),
        (RuntimeError("Your credit balance is too low to access the API"), "no_credits"),
        (RuntimeError("invalid x-api-key"), "invalid_key"),
        (WithStatus("nope", 401), "invalid_key"),
        (RuntimeError("failed to connect to ollama"), "ollama_unreachable"),
        (RuntimeError("[Errno 61] Connection refused"), "ollama_unreachable"),
        (WithStatus("slow down", 429), "rate_limited"),
        (RuntimeError("Rate limit reached for gpt"), "rate_limited"),
        (WithStatus("busy", 529), "overloaded"),
        (RuntimeError('{"type":"overloaded_error"}'), "overloaded"),
        (WithStatus("down", 503), "overloaded"),
        (WithStatus("oops", 500), "server_error"),
        (RuntimeError("502 Bad Gateway"), "server_error"),
        (TimeoutError("read timed out"), "timeout"),
        (RuntimeError("Request timed out."), "timeout"),
        (RuntimeError("Connection error."), "network"),
        (RuntimeError("temporary failure in name resolution"), "network"),
        (RuntimeError("This model's maximum context length is 8192 tokens"), "context_too_long"),
        (RuntimeError("prompt is too long: 250000 tokens"), "context_too_long"),
        (RuntimeError('model "llama9" not found, try pulling it first'), "model_not_found"),
        (WithStatus("nothing here", 404), "model_not_found"),
        (RuntimeError("something odd"), "error"),
        (ModuleNotFoundError("No module named 'ddgs'", name="ddgs"), "missing_package"),
    ])
    def test_each_failure_is_sorted_by_its_cause(self, error, code):
        assert classify(error, "Claude").code == code

    def test_an_unrecognised_failure_shows_its_own_words(self):
        failure = classify(RuntimeError("something odd"))
        assert (failure.code, failure.message, failure.retryable, failure.fallback) == ("error", "something odd", False, False)

    def test_the_status_is_read_from_the_response_too(self):
        error = RuntimeError("boom")
        error.response = WithStatus("", 429)
        assert classify(error).code == "rate_limited"

    def test_the_message_names_the_provider(self):
        assert "Claude" in classify(WithStatus("x", 529), "Claude").message


class TestWhatToDoAboutIt:
    @pytest.mark.parametrize("code, error", [
        ("rate_limited", WithStatus("x", 429)), ("overloaded", WithStatus("x", 529)),
        ("server_error", WithStatus("x", 500)), ("timeout", TimeoutError("x")), ("network", RuntimeError("Connection error.")),
    ])
    def test_a_passing_problem_is_retried(self, code, error):
        failure = classify(error)
        assert failure.code == code and failure.retryable and all(d > 0 for d in failure.retries)

    @pytest.mark.parametrize("error", [
        RuntimeError("invalid x-api-key"), RuntimeError("insufficient_quota"), RuntimeError("failed to connect to ollama"),
        RuntimeError("maximum context length"), RuntimeError("something odd"), ModuleNotFoundError("No module named 'x'"),
    ])
    def test_a_problem_waiting_cannot_fix_is_not_retried(self, error):
        assert not classify(error).retryable

    def test_waiting_is_longer_for_a_busy_service_than_for_a_blip(self):
        assert sum(llm_errors.BUSY_RETRIES) > sum(llm_errors.BRIEF_RETRIES)

    @pytest.mark.parametrize("error", [
        RuntimeError("invalid x-api-key"), RuntimeError("insufficient_quota"), RuntimeError("failed to connect to ollama"),
        WithStatus("x", 529), WithStatus("x", 429), TimeoutError("x"),
    ])
    def test_another_provider_could_answer_when_this_one_is_unusable(self, error):
        assert classify(error).fallback

    @pytest.mark.parametrize("error", [RuntimeError("maximum context length"), RuntimeError("something odd")])
    def test_another_provider_would_not_help_with_a_prompt_that_is_too_long(self, error):
        assert not classify(error).fallback


class Flaky:
    """Fails with `error` for the first `failures` calls, then answers."""

    def __init__(self, error, failures, answer="all good"):
        self.error, self.failures, self.calls, self.answer = error, failures, 0, answer

    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        yield AIMessageChunk(content=self.answer, response_metadata={"done_reason": "stop"})


@pytest.fixture
def waited(monkeypatch):
    seconds = []
    monkeypatch.setattr(agent, "_wait", seconds.append)
    return seconds


def run(chat_id, monkeypatch, llm_for):
    monkeypatch.setattr(agent, "_chat_llm", llm_for)
    return list(agent.run_agent(chat_id, "tell me about backtesting", [], chat_manager.get_config(chat_id)))


def answer_of(events):
    return "".join(e["content"] for e in events if e["type"] == "chunk")


class TestRetryingInTheLoop:
    def test_a_busy_service_is_retried_with_a_wait_and_the_user_sees_only_the_answer(self, chat_id, monkeypatch, waited):
        llm = Flaky(WithStatus("overloaded", 529), failures=2)
        events = run(chat_id, monkeypatch, lambda **kw: llm)
        assert answer_of(events) == "all good" and llm.calls == 3
        assert waited == list(llm_errors.BUSY_RETRIES[:2])
        assert [e for e in events if e["type"] in ("notice", "error")] == []

    def test_it_stops_retrying_and_raises_the_original_error(self, chat_id, monkeypatch, waited):
        llm = Flaky(WithStatus("overloaded", 529), failures=99)
        with pytest.raises(WithStatus):
            run(chat_id, monkeypatch, lambda **kw: llm)
        assert llm.calls == 1 + len(llm_errors.BUSY_RETRIES) and waited == list(llm_errors.BUSY_RETRIES)

    def test_a_wrong_key_is_not_retried(self, chat_id, monkeypatch, waited):
        llm = Flaky(RuntimeError("invalid x-api-key"), failures=99)
        with pytest.raises(RuntimeError):
            run(chat_id, monkeypatch, lambda **kw: llm)
        assert llm.calls == 1 and waited == []

    def test_a_model_without_tools_still_reaches_the_classic_fallback(self, chat_id, monkeypatch, waited):
        llm = Flaky(NotImplementedError("no tools"), failures=99)
        events = run(chat_id, monkeypatch, lambda **kw: llm)
        assert [e["type"] for e in events] == ["fallback"] and waited == []

    def test_a_failure_part_way_through_a_reply_is_not_quietly_started_over(self, chat_id, monkeypatch, waited):
        class Breaks(Flaky):
            def stream(self, messages):
                self.calls += 1
                yield AIMessageChunk(content="half an answ")
                raise WithStatus("overloaded", 529)

        llm = Breaks(None, 0)
        with pytest.raises(WithStatus):
            run(chat_id, monkeypatch, lambda **kw: llm)
        assert llm.calls == 1 and waited == []


class TestTheOptInFallback:
    def test_it_is_off_unless_the_user_names_a_provider(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        assert llm_config.fallback_provider() is None

    def test_a_cloud_fallback_needs_its_key(self, monkeypatch):
        monkeypatch.setenv("LLM_FALLBACK", "anthropic")
        assert llm_config.fallback_provider() is None
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        assert llm_config.fallback_provider() == "anthropic"

    def test_ollama_needs_no_key(self, monkeypatch):
        monkeypatch.setenv("LLM_FALLBACK", "ollama")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        assert llm_config.fallback_provider() == "ollama"

    def test_the_provider_already_in_use_is_not_its_own_fallback(self, monkeypatch):
        monkeypatch.setenv("LLM_FALLBACK", "ollama")
        assert llm_config.fallback_provider() is None          # ollama is the active provider with no keys

    def test_an_unknown_name_is_ignored(self, monkeypatch):
        monkeypatch.setenv("LLM_FALLBACK", "skynet")
        assert llm_config.fallback_provider() is None

    def test_when_the_main_provider_fails_the_other_answers_and_the_user_is_told(self, chat_id, monkeypatch, waited):
        monkeypatch.setattr(llm_config, "fallback_provider", lambda: "anthropic")
        primary = Flaky(RuntimeError("failed to connect to ollama"), failures=99)
        backup = Flaky(None, 0, answer="from the backup")
        events = run(chat_id, monkeypatch, lambda **kw: backup if kw.get("provider") == "anthropic" else primary)
        assert answer_of(events) == "from the backup"
        (notice,) = [e["message"] for e in events if e["type"] == "notice"]
        assert "Ollama didn't answer" in notice and "Claude" in notice

    def test_without_a_fallback_the_error_reaches_the_user(self, chat_id, monkeypatch, waited):
        monkeypatch.setattr(llm_config, "fallback_provider", lambda: None)
        with pytest.raises(RuntimeError, match="ollama"):
            run(chat_id, monkeypatch, lambda **kw: Flaky(RuntimeError("failed to connect to ollama"), failures=99))

    def test_a_problem_another_provider_cannot_fix_does_not_switch(self, chat_id, monkeypatch, waited):
        monkeypatch.setattr(llm_config, "fallback_provider", lambda: "anthropic")
        seen = []
        with pytest.raises(RuntimeError):
            run(chat_id, monkeypatch, lambda **kw: seen.append(kw) or Flaky(RuntimeError("maximum context length"), failures=99))
        assert all("provider" not in kw for kw in seen)

    def test_it_switches_once_not_back_and_forth(self, chat_id, monkeypatch, waited):
        monkeypatch.setattr(llm_config, "fallback_provider", lambda: "anthropic")
        with pytest.raises(RuntimeError):
            run(chat_id, monkeypatch, lambda **kw: Flaky(RuntimeError("failed to connect to ollama"), failures=99))
