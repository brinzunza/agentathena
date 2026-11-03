import hashlib
import os

import pytest
from fastapi.testclient import TestClient

import chat_manager
import main
import uploads


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def api_chat(client):
    return client.post("/chats", json={"name": "uploads"}).json()["id"]


@pytest.fixture
def processed(monkeypatch):
    """Stand in for the slow conversion/indexing; records what it was asked to process."""
    calls = []
    monkeypatch.setattr(main, "process_documents_background",
                        lambda upload_id, paths, chroma, files: calls.append((upload_id, list(paths), files)))
    return calls


def pdf(size=100, fill=b"x"):
    return (b"%PDF-1.4\n" + fill * size)[:size]


def begin(client, chat, name="hw.pdf", data=None, last_modified=1000):
    data = pdf() if data is None else data
    response = client.post(f"/chats/{chat}/uploads", json={"filename": name, "size": len(data), "last_modified": last_modified})
    return response, data


def put(client, chat, upload_id, offset, data, **headers):
    return client.put(f"/chats/{chat}/uploads/{upload_id}", params={"offset": offset}, content=data, headers=headers)


def test_a_whole_upload_in_chunks_then_processing(client, api_chat, processed):
    response, data = begin(client, api_chat, "homework.pdf", pdf(250))
    assert response.status_code == 200
    session = response.json()
    assert session["received"] == 0 and session["complete"] is False

    progress = []
    for offset in range(0, 250, 100):
        reply = put(client, api_chat, session["upload_id"], offset, data[offset:offset + 100])
        assert reply.status_code == 200
        progress.append(reply.json()["received"])
    assert progress == [100, 200, 250]
    assert client.get(f"/chats/{api_chat}/uploads/{session['upload_id']}").json()["complete"] is True

    result = client.post(f"/chats/{api_chat}/uploads/process", json={"upload_ids": [session["upload_id"]]})
    assert result.status_code == 200 and result.json()["status"] == "processing"
    (call,) = processed
    batch_id, paths, files_dir = call
    assert batch_id == result.json()["upload_id"]
    assert [os.path.basename(p) for p in paths] == ["homework.pdf"] and open(paths[0], "rb").read() == data
    # the batch can be polled with the existing status endpoint
    status = client.get(f"/documents/upload/status/{batch_id}").json()
    assert status["total_files"] == 1 and status["status"] == "converting"
    # and the session is gone
    assert client.get(f"/chats/{api_chat}/uploads/{session['upload_id']}").status_code == 404


def test_a_wrong_offset_is_a_409_that_says_where_to_resume_from(client, api_chat):
    response, data = begin(client, api_chat)
    upload_id = response.json()["upload_id"]
    put(client, api_chat, upload_id, 0, data[:40])
    clash = put(client, api_chat, upload_id, 0, data[:40])
    assert clash.status_code == 409
    assert clash.json()["detail"] == {"message": clash.json()["detail"]["message"], "code": "offset_mismatch", "received": 40}


def test_resuming_after_a_break_continues_from_what_the_server_stored(client, api_chat, processed):
    data = pdf(300)
    first, _ = begin(client, api_chat, "big.pdf", data, last_modified=5)
    upload_id = first.json()["upload_id"]
    put(client, api_chat, upload_id, 0, data[:100])
    put(client, api_chat, upload_id, 100, data[100:200])

    # ...connection drops; later the same file is announced again
    again, _ = begin(client, api_chat, "big.pdf", data, last_modified=5)
    assert again.json()["upload_id"] == upload_id and again.json()["received"] == 200 and again.json()["resumed"] is True
    assert put(client, api_chat, upload_id, 200, data[200:]).json()["complete"] is True
    client.post(f"/chats/{api_chat}/uploads/process", json={"upload_ids": [upload_id]})
    assert open(processed[0][1][0], "rb").read() == data


def test_chunk_checksums_are_verified(client, api_chat):
    response, data = begin(client, api_chat)
    upload_id = response.json()["upload_id"]
    damaged = put(client, api_chat, upload_id, 0, data[:40], **{"X-Chunk-SHA256": hashlib.sha256(b"other").hexdigest()})
    assert damaged.status_code == 422 and damaged.json()["detail"]["code"] == "checksum"
    good = put(client, api_chat, upload_id, 0, data[:40], **{"X-Chunk-SHA256": hashlib.sha256(data[:40]).hexdigest()})
    assert good.status_code == 200 and good.json()["received"] == 40


def test_oversized_chunks_are_refused(client, api_chat, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_CHUNK_BYTES", 30)
    response, data = begin(client, api_chat)
    upload_id = response.json()["upload_id"]
    refused = put(client, api_chat, upload_id, 0, data[:31])
    assert refused.status_code == 413 and refused.json()["detail"]["code"] == "chunk_too_big"
    assert put(client, api_chat, upload_id, 0, data[:30]).status_code == 200


def test_invalid_requests_are_rejected_with_clear_codes(client, api_chat):
    assert client.post(f"/chats/{api_chat}/uploads", json={"filename": "notes.txt", "size": 10}).json()["detail"]["code"] == "not_pdf"
    assert client.post(f"/chats/{api_chat}/uploads", json={"filename": "a.pdf", "size": 0}).status_code == 400
    assert client.post(f"/chats/{api_chat}/uploads", json={"filename": "a.pdf", "size": 10**10}).status_code == 413
    assert client.post(f"/chats/{api_chat}/uploads", json={"filename": "a.pdf"}).status_code == 422
    assert client.put(f"/chats/{api_chat}/uploads/{'0' * 32}", params={"offset": 0}, content=b"x").status_code == 404
    assert client.put(f"/chats/{api_chat}/uploads/{'0' * 32}", params={"offset": -1}, content=b"x").status_code == 422
    assert client.get(f"/chats/{api_chat}/uploads/not-an-id").status_code == 404


def test_unknown_chats_are_404_everywhere(client):
    assert client.post("/chats/nope/uploads", json={"filename": "a.pdf", "size": 10}).status_code == 404
    assert client.get("/chats/nope/uploads").status_code == 404
    assert client.get(f"/chats/nope/uploads/{'0' * 32}").status_code == 404
    assert client.put(f"/chats/nope/uploads/{'0' * 32}", params={"offset": 0}, content=b"x").status_code == 404
    assert client.delete(f"/chats/nope/uploads/{'0' * 32}").status_code == 404
    assert client.post("/chats/nope/uploads/process", json={"upload_ids": ["a" * 32]}).status_code == 404


def test_processing_waits_for_every_file_to_finish(client, api_chat, processed):
    done, data = begin(client, api_chat, "a.pdf", pdf(100), 1)
    partial, data2 = begin(client, api_chat, "b.pdf", pdf(100), 2)
    put(client, api_chat, done.json()["upload_id"], 0, data)
    put(client, api_chat, partial.json()["upload_id"], 0, data2[:30])
    ids = [done.json()["upload_id"], partial.json()["upload_id"]]
    refused = client.post(f"/chats/{api_chat}/uploads/process", json={"upload_ids": ids})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "incomplete"
    assert refused.json()["detail"]["received"] == 30 and processed == []

    put(client, api_chat, partial.json()["upload_id"], 30, data2[30:])
    assert client.post(f"/chats/{api_chat}/uploads/process", json={"upload_ids": ids}).status_code == 200
    assert [os.path.basename(p) for p in processed[0][1]] == ["a.pdf", "b.pdf"]


def test_process_needs_a_sensible_list_of_uploads(client, api_chat):
    assert client.post(f"/chats/{api_chat}/uploads/process", json={"upload_ids": []}).status_code == 422
    assert client.post(f"/chats/{api_chat}/uploads/process", json={"upload_ids": ["0" * 32]}).status_code == 404
    assert client.post(f"/chats/{api_chat}/uploads/process", json={"upload_ids": ["a"] * 99}).status_code == 422


def test_a_file_that_is_not_a_pdf_is_rejected_when_the_batch_is_processed(client, api_chat, processed):
    response, data = begin(client, api_chat, "fake.pdf", b"PK\x03\x04" + b"z" * 96)
    upload_id = response.json()["upload_id"]
    put(client, api_chat, upload_id, 0, data)
    refused = client.post(f"/chats/{api_chat}/uploads/process", json={"upload_ids": [upload_id]})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "not_pdf" and processed == []


def test_cancelling_discards_the_upload_and_is_repeatable(client, api_chat):
    response, data = begin(client, api_chat)
    upload_id = response.json()["upload_id"]
    put(client, api_chat, upload_id, 0, data[:40])
    assert client.delete(f"/chats/{api_chat}/uploads/{upload_id}").json() == {"status": "success"}
    assert client.delete(f"/chats/{api_chat}/uploads/{upload_id}").status_code == 200
    assert client.get(f"/chats/{api_chat}/uploads/{upload_id}").status_code == 404
    assert client.delete(f"/chats/{api_chat}/uploads/..%2F..").status_code in (404, 405)


def test_unfinished_uploads_can_be_listed(client, api_chat):
    assert client.get(f"/chats/{api_chat}/uploads").json() == {"uploads": []}
    response, data = begin(client, api_chat, "half.pdf", pdf(100))
    put(client, api_chat, response.json()["upload_id"], 0, data[:60])
    (listed,) = client.get(f"/chats/{api_chat}/uploads").json()["uploads"]
    assert (listed["filename"], listed["received"], listed["size"], listed["complete"]) == ("half.pdf", 60, 100, False)


def test_stale_uploads_are_removed_when_listing(client, api_chat, monkeypatch):
    response, _ = begin(client, api_chat)
    monkeypatch.setattr(uploads, "SESSION_TTL_SECONDS", -1)
    assert client.get(f"/chats/{api_chat}/uploads").json() == {"uploads": []}


def test_the_server_clears_stale_uploads_on_startup(api_chat, monkeypatch):
    uploads.create_session(api_chat, "old.pdf", 100, 1)
    monkeypatch.setattr(uploads, "SESSION_TTL_SECONDS", -1)
    main.discard_stale_uploads()
    assert uploads.list_sessions(api_chat) == []


# --- the original one-shot upload route ---

def test_the_original_upload_route_no_longer_trusts_the_clients_filename(client, api_chat, processed):
    _, files_dir = chat_manager.get_chat_paths(api_chat)
    response = client.post(f"/chats/{api_chat}/documents/upload", files={"files": ("../../evil.pdf", pdf(50), "application/pdf")})
    assert response.status_code == 200
    assert os.listdir(files_dir) == ["evil.pdf"]
    assert not os.path.exists(os.path.join(os.path.dirname(files_dir), "evil.pdf"))
    assert client.post(f"/chats/{api_chat}/documents/upload", files={"files": ("notes.txt", b"hi", "text/plain")}).status_code == 400
