import chat_manager
import pytest
from fastapi.testclient import TestClient

import main


def chat_with(name, *messages):
    chat = chat_manager.create_chat(name)["id"]
    chat_manager.save_messages(chat, [{"role": role, "content": content} for role, content in messages])
    return chat


def test_finds_messages_by_text_ignoring_case():
    chat = chat_with("trip", ("user", "Where is the Eiffel Tower?"), ("assistant", "In Paris, France."))
    results = chat_manager.search_chats("EIFFEL")
    assert [(r["chat_id"], r["index"], r["role"]) for r in results] == [(chat, 0, "user")]
    assert "Eiffel" in results[0]["snippet"] and results[0]["chat_name"] == "trip"


def test_every_word_must_appear_in_any_order():
    chat_with("a", ("user", "red apples and green pears"), ("user", "only red things"))
    assert [r["index"] for r in chat_manager.search_chats("pears red")] == [0]
    assert chat_manager.search_chats("red bananas") == []


def test_a_chat_is_found_by_its_name_too():
    chat = chat_with("Soccer scores", ("user", "hello"))
    [hit] = chat_manager.search_chats("soccer")
    assert (hit["chat_id"], hit["index"], hit["role"]) == (chat, None, "chat")


def test_system_and_error_messages_are_not_searched():
    chat_with("a", ("system", "Database reset successfully"), ("error", "reset failed: boom"))
    assert chat_manager.search_chats("reset") == []


def test_newest_chat_and_newest_message_come_first(monkeypatch):
    first = chat_with("one", ("user", "needle early"), ("user", "needle late"))
    second = chat_with("two", ("user", "needle"))
    results = chat_manager.search_chats("needle")
    assert [(r["chat_id"], r["index"]) for r in results] == [(second, 0), (first, 1), (first, 0)]


def test_results_are_capped_per_chat_and_overall():
    chat_with("busy", *[("user", f"needle {i}") for i in range(20)])
    for i in range(10):
        chat_with(f"c{i}", ("user", "needle"))
    assert len([r for r in chat_manager.search_chats("needle", per_chat=5) if r["chat_name"] == "busy"]) == 5
    assert len(chat_manager.search_chats("needle", limit=7)) == 7


def test_the_snippet_is_one_short_line_around_the_match():
    text = "intro " * 60 + "the PURPLE word\n\nwith newlines " + "tail " * 60
    chat_with("long", ("assistant", text))
    [hit] = chat_manager.search_chats("purple")
    assert "PURPLE" in hit["snippet"] and "\n" not in hit["snippet"]
    assert len(hit["snippet"]) <= chat_manager.SEARCH_SNIPPET_CHARS + 4
    assert hit["snippet"].startswith("…") and hit["snippet"].endswith("…")


@pytest.mark.parametrize("query", ["", "   "])
def test_a_blank_query_finds_nothing(query):
    chat_with("a", ("user", "anything"))
    assert chat_manager.search_chats(query) == []


def test_odd_message_shapes_do_not_break_the_search():
    chat_with("a", ("user", None), ("assistant", ["not", "text"]), ("assistant", "needle"))
    assert [r["index"] for r in chat_manager.search_chats("needle")] == [2]


def test_the_endpoint_returns_results():
    chat = chat_with("a", ("user", "find me"))
    response = TestClient(main.app).get("/chats/search", params={"q": " find "})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "find" and [(r["chat_id"], r["index"]) for r in body["results"]] == [(chat, 0)]
    assert TestClient(main.app).get("/chats/search").json()["results"] == []
