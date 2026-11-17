import pytest

import agent
import agent_tools
import chat_manager
from fakes import ScriptedLLM, call, text
from redaction import redact

KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"


class TestWhatIsMasked:
    @pytest.mark.parametrize("secret, label", [
        (KEY, "anthropic key"),
        ("sk-proj-abcdefghijklmnopqrstuvwxyz012345", "api key"),
        ("sk-abcdefghijklmnopqrstuvwxyz012345", "api key"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github token"),
        ("github_pat_" + "a1B2c3D4e5" * 5, "github token"),
        ("AKIAIOSFODNN7EXAMPLE", "aws key"),
        ("AIzaSyA-abcdefghijklmnopqrstuvwxyz01234", "google key"),
        ("xoxb-123456789012-abcdefghijkl", "slack token"),
        ("sk_live_abcdefghijklmnop1234", "stripe key"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "token"),
    ])
    def test_well_known_formats(self, secret, label):
        assert redact(f"here: {secret} done") == f"here: [redacted {label}] done"

    def test_a_private_key_block(self):
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\nmore\n-----END RSA PRIVATE KEY-----"
        assert redact(f"before\n{block}\nafter") == "before\n[redacted private key]\nafter"

    def test_a_private_key_block_cut_short_is_still_masked(self):
        assert redact("-----BEGIN PRIVATE KEY-----\nMIIEpAIBAAKCAQEA") == "[redacted private key]"

    @pytest.mark.parametrize("line, kept", [
        ("OPENAI_API_KEY=abcd1234efgh5678", "OPENAI_API_KEY="),
        ("export GITHUB_TOKEN=abcd1234efgh5678", "export GITHUB_TOKEN="),
        ('"client_secret": "abcd1234efgh5678"', '"client_secret": "'),
        ("password: hunter2hunter2", "password: "),
        ("DB_PASSWORD = 'correct-horse-battery'", "DB_PASSWORD = '"),
    ])
    def test_a_value_named_as_a_secret_loses_only_the_value(self, line, kept):
        assert redact(line) == f"{kept}[redacted]" + ("\"" if line.endswith('"') else "'" if line.endswith("'") else "")

    def test_a_bearer_header_keeps_its_label(self):
        assert redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456") == "Authorization: Bearer [redacted]"

    def test_a_password_inside_a_url(self):
        assert redact("clone https://bob:s3cretpass@example.com/repo.git") == "clone https://bob:[redacted]@example.com/repo.git"

    def test_several_in_one_text(self):
        out = redact(f"a={KEY}\nGITHUB_TOKEN=abcd1234efgh5678")
        assert KEY not in out and "abcd1234efgh5678" not in out and out.count("[redacted") == 2


class TestWhatIsLeftAlone:
    @pytest.mark.parametrize("ordinary", [
        "password = input()", "password: ", "tokens = 12", "max_tokens=4096", "api_key_len = len(key)",
        "sk-learn is a nickname", "import sklearn", "commit 3f786850e387550fdab836ed7e6dc881de23001b",
        "password = 'your-password-here'", "API_KEY=xxxxxxxxxxxx", "token = None", "secret = '<your secret>'",
        "the token ring network", "https://example.com/path?x=1", "user@example.com", "12:30:45", "",
    ])
    def test_ordinary_text(self, ordinary):
        assert redact(ordinary) == ordinary

    def test_none_comes_back_as_none(self):
        assert redact(None) is None

    def test_masking_twice_changes_nothing_more(self):
        once = redact(f"x {KEY} OPENAI_API_KEY=abcd1234efgh5678")
        assert redact(once) == once


def run(chat_id, monkeypatch, llm, mode="bypass", question="run python to print my environment"):
    monkeypatch.setattr(agent, "_chat_llm", lambda **kw: llm)
    config = {**chat_manager.get_config(chat_id), "permission_mode": mode}
    return list(agent.run_agent(chat_id, question, [], config))


class TestWhereItApplies:
    def test_code_output_is_masked_before_the_model_sees_it_and_before_it_is_shown(self, chat_id, monkeypatch):
        monkeypatch.setattr(agent_tools, "run_python_code", lambda code, workspace: f"ANTHROPIC_API_KEY={KEY}\nok")
        llm = ScriptedLLM(call("run_python", {"code": "print(1)"}), text("done"))
        events = run(chat_id, monkeypatch, llm)
        shown = next(e for e in events if e["type"] == "step_result")["result"]
        assert KEY not in shown and "[redacted" in shown and "ok" in shown
        assert KEY not in llm.calls[1][-1].content              # what the model reads next

    def test_a_secret_split_by_the_preview_cut_is_not_left_half_showing(self, chat_id, monkeypatch):
        padding = "x" * (agent.RESULT_PREVIEW_CHARS - 20)
        monkeypatch.setattr(agent_tools, "run_python_code", lambda code, workspace: f"{padding} {KEY}")
        events = run(chat_id, monkeypatch, ScriptedLLM(call("run_python", {"code": "print(1)"}), text("done")))
        shown = next(e for e in events if e["type"] == "step_result")["result"]
        assert "sk-ant" not in shown

    def test_a_document_the_user_uploaded_is_what_it_is_for_the_model_but_masked_where_it_is_shown(self, chat_id, monkeypatch):
        def read_document(self, name: str, offset: int = 0) -> str:
            """Read a document."""
            return "notes\nAPI_KEY=abcd1234efgh5678"

        monkeypatch.setattr(agent_tools.ToolBox, "read_document", read_document)
        monkeypatch.setattr(agent_tools.ToolBox, "document_names", lambda self: ["notes"])
        monkeypatch.setattr(agent_tools.ToolBox, "has_documents", property(lambda self: True))
        llm = ScriptedLLM(call("read_document", {"name": "notes"}), text("done"))
        events = run(chat_id, monkeypatch, llm, question="what is in my notes?")
        assert "abcd1234efgh5678" not in next(e for e in events if e["type"] == "step_result")["result"]

    def test_an_unrecognised_provider_error_is_masked_before_it_reaches_the_user(self):
        import llm_errors
        failure = llm_errors.classify(RuntimeError(f"bad request for key {KEY}"))
        assert failure.code == "error" and KEY not in failure.message

    def test_the_server_log_line_for_a_message_is_masked(self, chat_id, monkeypatch, capsys):
        import main
        from fastapi.testclient import TestClient
        monkeypatch.setattr(main, "run_agent", lambda *a: iter([{"type": "complete", "response_time": 0, "steps": 0}]))
        TestClient(main.app).post(f"/chats/{chat_id}/qa/chat/stream", json={"content": f"my key is {KEY}"})
        assert KEY not in capsys.readouterr().out
