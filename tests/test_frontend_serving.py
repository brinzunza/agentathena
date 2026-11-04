import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def build(tmp_path, monkeypatch):
    folder = tmp_path / "build"
    (folder / "static" / "js").mkdir(parents=True)
    (folder / "index.html").write_text("<html>athena</html>")
    (folder / "static" / "js" / "main.abc123.js").write_text("console.log(1)")
    (folder / "manifest.json").write_text("{}")
    (tmp_path / "secret.txt").write_text("keep out")
    monkeypatch.setattr(main, "FRONTEND_BUILD", str(folder))
    return folder


@pytest.fixture
def client():
    return TestClient(main.app)


def test_the_root_serves_the_app_when_it_has_been_built(client, build):
    response = client.get("/")
    assert response.status_code == 200 and "athena" in response.text
    assert response.headers["cache-control"] == "no-cache"        # a new build must show up straight away


def test_hashed_assets_are_cached_for_good_and_other_files_are_not(client, build):
    asset = client.get("/static/js/main.abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert client.get("/manifest.json").headers["cache-control"] == "no-cache"


def test_a_deep_link_gets_the_app_so_a_reload_works(client, build):
    response = client.get("/some/page")
    assert response.status_code == 200 and "athena" in response.text


def test_api_paths_are_never_answered_with_the_app(client, build):
    assert client.get("/chats/nope/whatever").status_code == 404
    assert client.get("/chats/nope/whatever").headers["content-type"].startswith("application/json")
    assert client.get("/settings/unknown").status_code == 404
    assert client.get("/health").json() == {"status": "healthy"}
    assert isinstance(client.get("/chats").json(), list)


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/static/../../secret.txt",
                                  "/..%2fsecret.txt", "/static/%2e%2e/%2e%2e/secret.txt"])
def test_files_outside_the_build_folder_are_never_served(client, build, path):
    response = client.get(path)
    assert "keep out" not in response.text


def test_without_a_build_the_root_is_the_api_message_and_other_paths_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "FRONTEND_BUILD", str(tmp_path / "missing"))
    assert client.get("/").json() == {"message": "ai assistant api"}
    assert client.get("/some/page").status_code == 404
