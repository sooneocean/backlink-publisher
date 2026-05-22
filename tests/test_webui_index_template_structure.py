"""index.html tab partial structure — Plan B Unit 2 (create_app() fixture pattern)."""

from __future__ import annotations

import pytest

from webui_app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_all_three_tab_panes_rendered(client):
    """GET / renders all three tab panes via {% include %}."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'id="newPanel"' in body
    assert 'id="historyPanel"' in body
    assert 'id="batchPanel"' in body


def test_history_tab_active_via_query_param(client):
    """GET /?section=history sets historyPanel as the active tab."""
    resp = client.get("/?section=history")
    assert resp.status_code == 200
    body = resp.data.decode()
    # historyPanel should have show active class when history_active is True
    assert "historyPanel" in body


def test_empty_history_store_renders_without_error(client):
    """GET / with empty history store renders without UndefinedError."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'id="historyPanel"' in body


def test_empty_profiles_renders_batch_panel(client):
    """GET / with empty profiles still renders batchPanel without error."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'id="batchPanel"' in body


def test_no_tab_pane_in_index_html_source():
    """index.html itself must not contain any inline tab-pane divs."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1]
        / "webui_app" / "templates" / "index.html"
    ).read_text(encoding="utf-8")
    assert "tab-pane" not in src, (
        "tab-pane found in index.html — should be in _tab_*.html partials"
    )


def test_index_html_includes_all_three_partials():
    """index.html must contain exactly three {% include '_tab_*.html' %} tags."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1]
        / "webui_app" / "templates" / "index.html"
    ).read_text(encoding="utf-8")
    assert "{% include '_tab_new.html' %}" in src
    assert "{% include '_tab_history.html' %}" in src
    assert "{% include '_tab_batch.html' %}" in src


def test_korean_language_option_in_rendered_page(client):
    """GET / must include value="ko" in the target_language select."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'value="ko"' in resp.data


def test_korean_language_option_label(client):
    """The Korean option must be labelled 한국어 (韩文)."""
    resp = client.get("/")
    body = resp.data.decode("utf-8")
    assert "한국어 (韩文)" in body
