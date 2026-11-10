"""Indexing must not redo documents that are already in a chat's collection."""

import os

import document_processor


class FakeStore:
    def __init__(self, indexed_paths):
        self.metadatas = [{"file_name": p} for p in indexed_paths]
        self.added = []

    def get(self, include=None):
        return {"metadatas": self.metadatas}

    def add_documents(self, docs):
        self.added.extend(docs)


def test_files_already_indexed_under_another_chats_folder_are_not_indexed_again(tmp_path, monkeypatch):
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "notes.md").write_text("old")
    (folder / "new.md").write_text("new")
    # The collection was copied from another chat, so its chunks name that chat's folder
    store = FakeStore(["chats/some-other-chat/files/notes.md"])
    monkeypatch.setattr(document_processor, "_get_vectorstore", lambda path: store)
    processed = []

    def fake_process(files, *args, **kwargs):
        processed.extend(os.path.basename(f) for f in files)
        return ["chunk"]

    monkeypatch.setattr(document_processor, "_process_markdown_files", fake_process)
    document_processor.index_documents(str(folder), str(tmp_path / "db"))

    assert processed == ["new.md"]
    assert store.added == ["chunk"]


def test_nothing_is_indexed_when_every_file_is_already_there(tmp_path, monkeypatch):
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "notes.md").write_text("old")
    store = FakeStore(["chats/elsewhere/files/notes.md"])
    monkeypatch.setattr(document_processor, "_get_vectorstore", lambda path: store)
    monkeypatch.setattr(document_processor, "_process_markdown_files",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))
    document_processor.index_documents(str(folder), str(tmp_path / "db"))
    assert store.added == []
