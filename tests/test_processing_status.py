import os
import re

import pytest

import main
import processing_status as pstatus


def entry(status, chat="c1", finished_at=None):
    value = {"chat_id": chat, "status": status}
    if finished_at is not None:
        value["finished_at"] = finished_at
    return value


class TestStatuses:
    def test_only_completed_and_error_are_terminal(self):
        assert pstatus.TERMINAL == {pstatus.COMPLETED, pstatus.ERROR}

    @pytest.mark.parametrize("status", [pstatus.UPLOADING, pstatus.CONVERTING, pstatus.INDEXING, pstatus.CLEANUP])
    def test_every_stage_in_between_is_active(self, status):
        assert pstatus.is_active(entry(status))

    @pytest.mark.parametrize("status", [pstatus.COMPLETED, pstatus.ERROR])
    def test_finished_jobs_are_not_active(self, status):
        assert not pstatus.is_active(entry(status))

    def test_every_stage_has_a_label_for_failure_messages(self):
        for stage in (pstatus.UPLOADING, pstatus.CONVERTING, pstatus.INDEXING, pstatus.CLEANUP):
            assert stage in pstatus.STAGE_LABELS

    def test_the_failure_message_names_the_stage_and_the_reason(self):
        message = pstatus.failure_message(pstatus.INDEXING, RuntimeError("model unreachable"))
        assert message == "processing failed while indexing the documents: model unreachable"

    def test_an_unknown_stage_still_gives_a_readable_message(self):
        assert pstatus.failure_message("mystery", ValueError("x")) == "processing failed while processing: x"

    def test_main_never_compares_against_a_bare_status_string(self):
        # Statuses are defined once, in processing_status; a literal here would drift from it
        source = open(main.__file__, encoding="utf-8").read()
        literals = re.findall(r'"status":\s*"(uploading|converting|indexing|cleanup|completed)"', source)
        assert literals == []


class TestPruning:
    def test_old_finished_jobs_are_dropped(self):
        progress = {"old": entry(pstatus.COMPLETED, finished_at=0), "failed": entry(pstatus.ERROR, finished_at=0)}
        assert pstatus.prune(progress, now=pstatus.FINISHED_TTL_SECONDS + 1) == 2
        assert progress == {}

    def test_recent_finished_jobs_stay_so_the_browser_can_read_the_result(self):
        progress = {"fresh": entry(pstatus.COMPLETED, finished_at=100)}
        assert pstatus.prune(progress, now=100 + pstatus.FINISHED_TTL_SECONDS - 1) == 0
        assert "fresh" in progress

    def test_jobs_still_working_are_never_dropped_however_old(self):
        progress = {"busy": entry(pstatus.INDEXING)}
        assert pstatus.prune(progress, now=10 ** 9) == 0
        assert "busy" in progress

    def test_a_finished_job_without_a_timestamp_is_kept_not_guessed_at(self):
        progress = {"odd": entry(pstatus.COMPLETED)}
        assert pstatus.prune(progress, now=10 ** 9) == 0

    def test_finish_stamps_the_time(self):
        job = entry(pstatus.COMPLETED)
        pstatus.finish(job, now=42.0)
        assert job["finished_at"] == 42.0


class TestTheBackgroundJob:
    @pytest.fixture(autouse=True)
    def clean(self):
        main.upload_progress.clear()
        yield
        main.upload_progress.clear()

    def start(self, upload_id="u1", chat="c1"):
        main.upload_progress[upload_id] = {
            "chat_id": chat, "status": pstatus.CONVERTING, "progress": 10, "message": "queued",
            "current_file": None, "total_files": 1, "processed_files": 0,
        }

    def test_a_failure_says_which_stage_and_keeps_how_far_it_got(self, monkeypatch, tmp_path):
        self.start()
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(main, "pdf_to_markdown", lambda *a, **k: None)

        def fail(*a, progress_callback=None, **k):
            progress_callback(3, 10, "a.md")             # got to the indexing stage, 3 chunks in
            raise RuntimeError("embedding model not found")

        monkeypatch.setattr(main, "index_documents", fail)
        main.process_documents_background("u1", [str(pdf)], str(tmp_path / "db"), str(tmp_path))
        job = main.upload_progress["u1"]
        assert job["status"] == pstatus.ERROR
        assert job["message"] == "processing failed while indexing the documents: embedding model not found"
        assert job["progress"] == 50 + int(3 / 10 * 40)     # not reset to zero
        assert "finished_at" in job

    def test_a_failure_during_conversion_is_labelled_as_conversion(self, monkeypatch, tmp_path):
        self.start()
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        def fail(*a, **k):
            raise ValueError("corrupt pdf")

        monkeypatch.setattr(main, "pdf_to_markdown", fail)
        main.process_documents_background("u1", [str(pdf)], str(tmp_path / "db"), str(tmp_path))
        assert main.upload_progress["u1"]["message"] == "processing failed while converting the PDFs: corrupt pdf"

    def test_success_is_stamped_finished(self, monkeypatch, tmp_path):
        self.start()
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(main, "pdf_to_markdown", lambda *a, **k: None)
        monkeypatch.setattr(main, "index_documents", lambda *a, **k: None)
        main.process_documents_background("u1", [str(pdf)], str(tmp_path / "db"), str(tmp_path))
        job = main.upload_progress["u1"]
        assert job["status"] == pstatus.COMPLETED and job["progress"] == 100 and "finished_at" in job
        assert not os.path.exists(pdf)                       # the cleanup stage ran

    def test_a_chat_is_busy_only_while_its_job_is_active(self):
        self.start("working", "busy-chat")
        main.upload_progress["done"] = {**main.upload_progress["working"], "chat_id": "idle-chat", "status": pstatus.COMPLETED}
        main.upload_progress["failed"] = {**main.upload_progress["working"], "chat_id": "broken-chat", "status": pstatus.ERROR}
        assert main.in_progress_upload_chat_ids() == {"busy-chat"}


class TestTheBrowserKnowsTheSameStates:
    """frontend/src/uploadStatus.js names the same states; adding one here without there would break the queue."""

    def browser_constants(self):
        source = open(os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "uploadStatus.js"),
                      encoding="utf-8").read()
        return dict(re.findall(r"export const (\w+) = '(\w+)';", source))

    def test_every_server_status_has_a_browser_constant_with_the_same_value(self):
        known = set(self.browser_constants().values())
        assert set(pstatus.ALL) <= known, sorted(set(pstatus.ALL) - known)

    def test_the_browser_adds_only_interrupted(self):
        extra = set(self.browser_constants().values()) - set(pstatus.ALL)
        assert extra == {"interrupted"}
