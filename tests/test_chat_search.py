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


# --- the full-text index behind search (chat_index.py) ---

import chat_index
import os
import time


def search(query):
    return [(r["chat_id"], r["index"]) for r in chat_manager.search_chats(query)]


class TestTheIndexKeepsUp:
    def test_a_message_added_after_the_first_search_is_found_by_the_next(self):
        chat = chat_with("a", ("user", "first thing"))
        assert search("second") == []
        chat_manager.update_messages(chat, lambda m: [*m, {"role": "user", "content": "the second thing"}])
        assert search("second") == [(chat, 1)]

    def test_a_deleted_chat_stops_turning_up(self):
        chat = chat_with("a", ("user", "needle"))
        assert search("needle") == [(chat, 0)]
        chat_manager.delete_chat(chat)
        assert search("needle") == []

    def test_a_rename_is_found_by_the_new_name_and_not_the_old(self):
        chat = chat_with("old name", ("user", "hello"))
        assert search("old") == [(chat, None)]
        chat_manager.rename_chat(chat, "fresh title")
        assert search("old") == [] and search("fresh") == [(chat, None)]

    def test_a_message_that_was_removed_is_no_longer_found(self):
        chat = chat_with("a", ("user", "needle"), ("user", "other"))
        assert search("needle") == [(chat, 0)]
        chat_manager.save_messages(chat, [{"role": "user", "content": "other"}])
        assert search("needle") == []

    def test_a_chat_still_being_copied_is_not_searchable(self):
        chat = chat_manager.create_chat("half", building=True)["id"]
        chat_manager.save_messages(chat, [{"role": "user", "content": "secret needle"}])
        assert search("needle") == []

    def test_the_index_can_be_deleted_and_comes_back_by_itself(self):
        chat = chat_with("a", ("user", "needle"))
        assert search("needle") == [(chat, 0)]
        os.remove(os.path.join(chat_manager.CHATS_ROOT, chat_index.INDEX_NAME))
        assert search("needle") == [(chat, 0)]

    def test_only_a_chat_that_changed_is_read_again(self):
        first = chat_with("a", ("user", "one"))
        chat_with("b", ("user", "two"))
        connection = chat_index._connect(chat_manager.CHATS_ROOT)
        ids = [c["id"] for c in chat_manager.list_chats()]
        assert chat_index.refresh(connection, chat_manager.CHATS_ROOT, ids) == 2
        assert chat_index.refresh(connection, chat_manager.CHATS_ROOT, ids) == 0
        time.sleep(0.01)
        chat_manager.update_messages(first, lambda m: [*m, {"role": "user", "content": "more"}])
        assert chat_index.refresh(connection, chat_manager.CHATS_ROOT, ids) == 1
        connection.close()


class TestWhatTheIndexMustNotChange:
    def test_a_part_of_a_word_is_found_as_it_was_by_the_scan(self):
        chat = chat_with("a", ("user", "Where is the Eiffel Tower?"))
        assert search("iffel") == [(chat, 0)]

    @pytest.mark.parametrize("query", ["ab", "a", "an"])
    def test_words_too_short_for_the_index_still_work(self, query):
        chat = chat_with("x", ("user", "an abacus and a banana"))
        chat_with("y", ("user", "nothing here"))
        assert search(query) == [(chat, 0)]

    def test_short_and_long_words_together(self):
        chat = chat_with("a", ("user", "a red apple"))
        chat_with("b", ("user", "a red pear"))
        assert search("a apple") == [(chat, 0)]

    @pytest.mark.parametrize("query", ['"quoted"', "a*b", "NEAR(x y)", "col:thing", "-minus", "AND OR NOT", "(", "'", "x AND"])
    def test_characters_with_a_meaning_to_the_index_mean_nothing_special(self, query):
        chat_with("a", ("user", "plain text"))
        chat_manager.search_chats(query)             # must not raise

    def test_a_quote_inside_the_text_can_be_searched_for(self):
        chat = chat_with("a", ("user", 'she said "hello there" twice'))
        assert search('"hello') == [(chat, 0)]

    def test_case_is_ignored_in_the_text_and_in_the_query(self):
        chat = chat_with("a", ("user", "Quarterly REPORT"))
        assert search("quarterly report") == [(chat, 0)] == search("QUARTERLY report")

    def test_unicode_text_is_found(self):
        chat = chat_with("a", ("user", "café au lait, 日本語のテキスト"))
        assert search("café") == [(chat, 0)] and search("日本語") == [(chat, 0)]


class TestWithoutTheIndex:
    def test_when_sqlite_cannot_provide_it_search_scans_the_files(self, monkeypatch):
        chat = chat_with("a", ("user", "needle in a haystack"))
        monkeypatch.setattr(chat_index, "available", lambda: False)
        assert search("needle") == [(chat, 0)]
        assert not os.path.exists(os.path.join(chat_manager.CHATS_ROOT, chat_index.INDEX_NAME))

    def test_when_the_index_fails_midway_search_scans_instead(self, monkeypatch):
        chat = chat_with("a", ("user", "needle"))

        def broken(*args, **kwargs):
            raise chat_index.IndexUnavailable("disk I/O error")

        monkeypatch.setattr(chat_index, "candidates", broken)
        assert search("needle") == [(chat, 0)]

    def test_a_corrupt_index_file_is_reported_not_trusted(self):
        chat = chat_with("a", ("user", "needle"))
        with open(os.path.join(chat_manager.CHATS_ROOT, chat_index.INDEX_NAME), "wb") as f:
            f.write(b"this is not a database" * 100)
        assert search("needle") == [(chat, 0)]
