import hashlib
import os
import threading
import time

import pytest

import uploads
from uploads import UploadError


def pdf(size=100, fill=b"x"):
    """Bytes that pass for a PDF."""
    return (b"%PDF-1.4\n" + fill * size)[:size]


def start(chat_id, name="hw.pdf", data=None, last_modified=1000):
    data = pdf() if data is None else data
    session = uploads.create_session(chat_id, name, len(data), last_modified)
    return session, data


def send(chat_id, session, data, start_at=0, step=40):
    """Send `data` from `start_at` in `step`-byte chunks; returns the last reply."""
    reply = None
    for offset in range(start_at, len(data), step):
        reply = uploads.append_chunk(chat_id, session["upload_id"], offset, data[offset:offset + step])
    return reply


def error_of(func, *args, **kwargs):
    with pytest.raises(UploadError) as info:
        func(*args, **kwargs)
    return info.value


# --- filenames ---

@pytest.mark.parametrize("given, safe", [
    ("hw.pdf", "hw.pdf"),
    ("HW 2.PDF", "HW 2.pdf"),                       # the extension is normalised
    ("../../etc/evil.pdf", "evil.pdf"),             # folders are dropped
    ("C:\\Users\\me\\doc.pdf", "doc.pdf"),
    ("  spaced  .pdf", "spaced.pdf"),
    ("tab\tname.pdf", "tabname.pdf"),               # control characters are dropped
    ("..hidden.pdf", "hidden.pdf"),
    ("x" * 400 + ".pdf", "x" * 150 + ".pdf"),
])
def test_filenames_are_made_safe(given, safe):
    assert uploads.sanitize_filename(given) == safe


@pytest.mark.parametrize("bad", ["notes.txt", "archive.pdf.zip", ".pdf", "", "   .pdf", "dir/", "pdf"])
def test_non_pdf_names_are_refused(bad):
    assert error_of(uploads.sanitize_filename, bad).status == 400


# --- starting an upload ---

def test_a_new_session_starts_empty_with_a_suggested_chunk_size(chat_id):
    session, _ = start(chat_id)
    assert session["received"] == 0 and session["size"] == 100 and session["filename"] == "hw.pdf"
    assert session["complete"] is False and session["resumed"] is False
    assert session["chunk_size"] == uploads.CHUNK_BYTES
    assert len(session["upload_id"]) == 32


def test_empty_and_oversized_files_are_refused(chat_id):
    assert error_of(uploads.create_session, chat_id, "a.pdf", 0).code == "empty"
    assert error_of(uploads.create_session, chat_id, "a.pdf", -5).status == 400
    too_big = error_of(uploads.create_session, chat_id, "a.pdf", uploads.MAX_FILE_BYTES + 1)
    assert too_big.status == 413 and too_big.code == "too_big"
    uploads.create_session(chat_id, "a.pdf", uploads.MAX_FILE_BYTES)       # exactly the limit is fine


def test_announcing_the_same_file_again_resumes_the_same_session(chat_id):
    data = pdf(200)
    first, _ = start(chat_id, data=data)
    send(chat_id, first, data[:120], step=60)
    again = uploads.create_session(chat_id, "hw.pdf", len(data), 1000)      # e.g. after a page reload
    assert again["upload_id"] == first["upload_id"] and again["received"] == 120 and again["resumed"] is True


def test_a_different_file_gets_its_own_session(chat_id):
    first, _ = start(chat_id, data=pdf(100), last_modified=1)
    for other in ((pdf(101), 1), (pdf(100), 2)):                            # different size, different modified time
        session = uploads.create_session(chat_id, "hw.pdf", len(other[0]), other[1])
        assert session["upload_id"] != first["upload_id"]
    assert uploads.create_session(chat_id, "other.pdf", 100, 1)["upload_id"] != first["upload_id"]


def test_sessions_are_per_chat(chat_id):
    import chat_manager
    other_chat = chat_manager.create_chat("other")["id"]
    first, _ = start(chat_id)
    second = uploads.create_session(other_chat, "hw.pdf", 100, 1000)
    assert second["upload_id"] != first["upload_id"]
    assert uploads.get_session(other_chat, first["upload_id"]) is None


def test_only_so_many_uploads_can_be_open_at_once(chat_id):
    for i in range(uploads.MAX_OPEN_SESSIONS):
        uploads.create_session(chat_id, f"f{i}.pdf", 100, i)
    assert error_of(uploads.create_session, chat_id, "one-too-many.pdf", 100, 99).status == 429
    uploads.create_session(chat_id, "f0.pdf", 100, 0)                       # resuming an open one is still fine


# --- sending chunks ---

def test_chunks_in_order_build_the_file_and_report_real_progress(chat_id):
    session, data = start(chat_id, data=pdf(100))
    received = []
    for offset in range(0, 100, 30):
        reply = uploads.append_chunk(chat_id, session["upload_id"], offset, data[offset:offset + 30])
        received.append((reply["received"], reply["complete"]))
    assert received == [(30, False), (60, False), (90, False), (100, True)]
    assert uploads.get_session(chat_id, session["upload_id"])["received"] == 100


def test_a_chunk_at_the_wrong_offset_is_refused_and_the_error_says_where_to_resume(chat_id):
    session, data = start(chat_id)
    uploads.append_chunk(chat_id, session["upload_id"], 0, data[:40])
    for wrong in (0, 10, 80):                                                # replayed, overlapping, skipping ahead
        error = error_of(uploads.append_chunk, chat_id, session["upload_id"], wrong, data[wrong:wrong + 10])
        assert (error.status, error.code, error.extra["received"]) == (409, "offset_mismatch", 40)
    assert uploads.get_session(chat_id, session["upload_id"])["received"] == 40      # nothing was written


def test_resending_a_chunk_whose_reply_was_lost_is_detected_not_duplicated(chat_id):
    session, data = start(chat_id)
    uploads.append_chunk(chat_id, session["upload_id"], 0, data[:50])        # arrived, but the reply never did
    retry = error_of(uploads.append_chunk, chat_id, session["upload_id"], 0, data[:50])
    assert retry.code == "offset_mismatch" and retry.extra["received"] == 50   # client sees it all arrived
    send(chat_id, session, data, start_at=50, step=50)
    uploads.finalize(chat_id, [session["upload_id"]], os.path.join(os.getcwd(), "out"))
    with open(os.path.join("out", "hw.pdf"), "rb") as f:
        assert f.read() == data


def test_a_chunk_cannot_run_past_the_end_of_the_file(chat_id):
    session, data = start(chat_id, data=pdf(100))
    error = error_of(uploads.append_chunk, chat_id, session["upload_id"], 0, data + b"extra")
    assert (error.status, error.code) == (400, "past_end")


def test_empty_and_oversized_chunks_are_refused(chat_id):
    session, data = start(chat_id)
    assert error_of(uploads.append_chunk, chat_id, session["upload_id"], 0, b"").code == "empty"
    big = b"x" * (uploads.MAX_CHUNK_BYTES + 1)
    assert error_of(uploads.append_chunk, chat_id, session["upload_id"], 0, big).status == 413


def test_a_damaged_chunk_is_caught_by_its_checksum_and_not_stored(chat_id):
    session, data = start(chat_id)
    good = hashlib.sha256(data[:40]).hexdigest()
    bad = hashlib.sha256(b"something else").hexdigest()
    error = error_of(uploads.append_chunk, chat_id, session["upload_id"], 0, data[:40], bad)
    assert (error.status, error.code) == (422, "checksum")
    assert uploads.get_session(chat_id, session["upload_id"])["received"] == 0
    reply = uploads.append_chunk(chat_id, session["upload_id"], 0, data[:40], good.upper())   # case doesn't matter
    assert reply["received"] == 40
    assert uploads.append_chunk(chat_id, session["upload_id"], 40, data[40:], None)["complete"]   # checksum is optional


def test_unknown_and_malformed_upload_ids_are_not_found(chat_id):
    for bad_id in ("0" * 32, "../../etc", "short", ""):
        assert error_of(uploads.append_chunk, chat_id, bad_id, 0, b"x").status == 404
    assert uploads.get_session(chat_id, "0" * 32) is None
    assert error_of(uploads.get_session, chat_id, "../..").status == 404


def test_a_write_that_was_cut_off_before_the_count_was_saved_is_overwritten_on_resume(chat_id):
    """A crash after the bytes hit the disk but before the stored count moved on must not corrupt the file."""
    session, data = start(chat_id, data=pdf(100))
    uploads.append_chunk(chat_id, session["upload_id"], 0, data[:40])
    part = os.path.join(uploads._session_dir(chat_id, session["upload_id"]), "data.part")
    with open(part, "ab") as f:
        f.write(b"JUNK-FROM-AN-INTERRUPTED-WRITE" * 20)                      # on disk, never counted - and longer than what's left
    send(chat_id, session, data, start_at=40, step=60)
    assert os.path.getsize(part) == len(data)                                # the stray tail is gone, not just overwritten
    out = os.path.join(os.getcwd(), "out")
    uploads.finalize(chat_id, [session["upload_id"]], out)
    with open(os.path.join(out, "hw.pdf"), "rb") as f:
        assert f.read() == data


def test_two_requests_racing_with_the_same_chunk_store_it_once(chat_id):
    session, data = start(chat_id, data=pdf(100))
    outcomes, barrier = [], threading.Barrier(6)

    def attempt():
        barrier.wait()
        try:
            outcomes.append(uploads.append_chunk(chat_id, session["upload_id"], 0, data[:50])["received"])
        except UploadError as e:
            outcomes.append(e.code)

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count(50) == 1 and outcomes.count("offset_mismatch") == 5
    assert uploads.get_session(chat_id, session["upload_id"])["received"] == 50


# --- finishing ---

def test_finalize_moves_the_finished_files_and_removes_their_sessions(chat_id, tmp_path):
    first, data1 = start(chat_id, "a.pdf", pdf(120, b"a"), 1)
    second, data2 = start(chat_id, "b.pdf", pdf(80, b"b"), 2)
    send(chat_id, first, data1)
    send(chat_id, second, data2)
    dest = str(tmp_path / "files")
    stored = uploads.finalize(chat_id, [second["upload_id"], first["upload_id"]], dest)
    assert [name for _, name in stored] == ["b.pdf", "a.pdf"]                 # in the order given
    assert open(stored[0][0], "rb").read() == data2 and open(stored[1][0], "rb").read() == data1
    assert uploads.list_sessions(chat_id) == []
    assert sorted(os.listdir(dest)) == ["a.pdf", "b.pdf"]


def test_finalize_refuses_the_whole_batch_if_any_upload_is_unfinished(chat_id, tmp_path):
    done, data1 = start(chat_id, "a.pdf", pdf(100), 1)
    partial, data2 = start(chat_id, "b.pdf", pdf(100), 2)
    send(chat_id, done, data1)
    send(chat_id, partial, data2[:50], step=50)
    dest = str(tmp_path / "files")
    error = error_of(uploads.finalize, chat_id, [done["upload_id"], partial["upload_id"]], dest)
    assert (error.status, error.code, error.extra["received"]) == (409, "incomplete", 50)
    assert not os.path.exists(dest) or os.listdir(dest) == []               # nothing was moved
    assert len(uploads.list_sessions(chat_id)) == 2                          # and both can still be finished


def test_finalize_rejects_a_file_that_is_not_a_pdf_and_discards_it(chat_id, tmp_path):
    session, data = start(chat_id, "fake.pdf", b"PK\x03\x04" + b"z" * 96)    # a zip renamed .pdf
    send(chat_id, session, data)
    error = error_of(uploads.finalize, chat_id, [session["upload_id"]], str(tmp_path / "files"))
    assert (error.status, error.code) == (400, "not_pdf")
    assert uploads.get_session(chat_id, session["upload_id"]) is None


def test_finalize_unknown_upload_is_not_found(chat_id, tmp_path):
    assert error_of(uploads.finalize, chat_id, ["0" * 32], str(tmp_path)).status == 404


def test_duplicate_names_in_one_batch_get_numbered(chat_id, tmp_path):
    ids = []
    for modified in (1, 2, 3):
        session, data = start(chat_id, "hw.pdf", pdf(100, bytes([97 + modified])), modified)
        send(chat_id, session, data)
        ids.append(session["upload_id"])
    stored = uploads.finalize(chat_id, ids, str(tmp_path / "files"))
    assert [name for _, name in stored] == ["hw.pdf", "hw (2).pdf", "hw (3).pdf"]


# --- cancelling and tidying up ---

def test_abort_discards_a_session_and_is_safe_to_repeat(chat_id):
    session, data = start(chat_id)
    send(chat_id, session, data[:50], step=50)
    uploads.abort(chat_id, session["upload_id"])
    uploads.abort(chat_id, session["upload_id"])
    assert uploads.get_session(chat_id, session["upload_id"]) is None
    assert not os.path.exists(uploads._session_dir(chat_id, session["upload_id"]))
    assert error_of(uploads.abort, chat_id, "../../x").status == 404


def test_stale_sessions_are_discarded_and_fresh_ones_kept(chat_id):
    old, _ = start(chat_id, "old.pdf", pdf(100), 1)
    fresh, _ = start(chat_id, "fresh.pdf", pdf(100), 2)
    later = time.time() + uploads.SESSION_TTL_SECONDS + 10
    meta = uploads._read_meta(chat_id, fresh["upload_id"])
    meta["updated_at"] = later - 5
    uploads._write_meta(chat_id, fresh["upload_id"], meta)
    assert uploads.cleanup_stale(chat_id, now=later) == 1
    assert uploads.get_session(chat_id, old["upload_id"]) is None
    assert uploads.get_session(chat_id, fresh["upload_id"]) is not None


def test_cleanup_covers_every_chat_and_folders_with_broken_metadata(chat_id):
    import chat_manager
    other = chat_manager.create_chat("other")["id"]
    for chat in (chat_id, other):
        start(chat, "a.pdf", pdf(100), 1)
    broken = os.path.join(uploads._uploads_dir(chat_id), "f" * 32)
    os.makedirs(broken)
    open(os.path.join(broken, "meta.json"), "w").write("{not json")
    assert uploads.cleanup_stale(now=time.time() + uploads.SESSION_TTL_SECONDS + 10) == 3
    assert uploads.list_sessions(chat_id) == [] and uploads.list_sessions(other) == []
    assert not os.path.exists(broken)


def test_sending_a_chunk_counts_as_activity_so_a_slow_upload_is_not_discarded(chat_id):
    session, data = start(chat_id, data=pdf(100))
    meta = uploads._read_meta(chat_id, session["upload_id"])
    meta["updated_at"] = time.time() - uploads.SESSION_TTL_SECONDS + 5      # about to expire
    uploads._write_meta(chat_id, session["upload_id"], meta)
    uploads.append_chunk(chat_id, session["upload_id"], 0, data[:10])
    assert uploads.cleanup_stale(chat_id, now=time.time() + 60) == 0


def test_an_upload_survives_a_server_restart(chat_id, tmp_path):
    """Everything lives on disk: a fresh process (module reloaded, no in-memory state) carries on from the stored count."""
    import importlib
    data = pdf(200)
    session, _ = start(chat_id, data=data)
    send(chat_id, session, data[:120], step=60)

    fresh = importlib.reload(uploads)                       # as if the backend had just started
    assert fresh._locks == {}
    resumed = fresh.create_session(chat_id, "hw.pdf", len(data), 1000)
    assert (resumed["upload_id"], resumed["received"], resumed["resumed"]) == (session["upload_id"], 120, True)

    fresh.append_chunk(chat_id, session["upload_id"], 120, data[120:])
    (path, name), = fresh.finalize(chat_id, [session["upload_id"]], str(tmp_path / "files"))
    assert name == "hw.pdf" and open(path, "rb").read() == data
