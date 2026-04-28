from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._office.helpers import artifact_editor, canvas_context, wopi_routes, wopi_store


@pytest.fixture()
def office_state(tmp_path, monkeypatch):
    workdir = tmp_path / "workdir"
    state = tmp_path / "state"
    documents = workdir / "documents"
    monkeypatch.setattr(wopi_store, "STATE_DIR", state)
    monkeypatch.setattr(wopi_store, "DB_PATH", state / "documents.sqlite3")
    monkeypatch.setattr(wopi_store, "BACKUP_DIR", state / "backups")
    monkeypatch.setattr(wopi_store, "DOCUMENTS_DIR", documents)
    monkeypatch.setattr(wopi_store, "WORKDIR", workdir)
    wopi_store.ensure_dirs()
    return {"workdir": workdir, "state": state, "documents": documents}


def test_check_file_info_has_no_nulls_and_token_is_scoped(office_state):
    doc = wopi_store.create_document("document", "Scope Test", "docx", "hello")
    session = wopi_store.create_session(doc["file_id"], "user-a", "write", "http://localhost:32080")

    token_info = wopi_store.validate_token(session["access_token"], doc["file_id"], require_write=True)
    info = wopi_store.check_file_info(doc["file_id"], token_info)

    assert all(value is not None for value in info.values())
    assert info["UserCanWrite"] is True
    assert info["ReadOnly"] is False
    assert info["SupportsLocks"] is True

    other = wopi_store.create_document("document", "Other", "docx", "")
    with pytest.raises(PermissionError):
        wopi_store.validate_token(session["access_token"], other["file_id"])


def test_path_traversal_and_symlink_escape_are_rejected(office_state, tmp_path):
    outside = tmp_path / "outside.docx"
    outside.write_bytes(wopi_store.template_bytes("document", "docx", "Outside", ""))

    with pytest.raises(PermissionError):
        wopi_store.register_document(outside)

    link = office_state["workdir"] / "escape.docx"
    link.symlink_to(outside)
    with pytest.raises(PermissionError):
        wopi_store.register_document(link)


def test_lock_conflicts_refresh_unlock_and_relock(office_state):
    doc = wopi_store.create_document("document", "Lock Test", "docx", "")
    session = wopi_store.create_session(doc["file_id"], "user-a", "write", "http://localhost:32080")

    ok, current = wopi_store.lock(doc["file_id"], "lock-a", session["session_id"], 120)
    assert ok is True
    assert current == "lock-a"

    ok, current = wopi_store.lock(doc["file_id"], "lock-b", session["session_id"], 120)
    assert ok is False
    assert current == "lock-a"

    ok, current = wopi_store.refresh_lock(doc["file_id"], "lock-a", 120)
    assert ok is True
    assert current == "lock-a"

    ok, current = wopi_store.unlock_and_relock(doc["file_id"], "lock-a", "lock-c", session["session_id"], 120)
    assert ok is True
    assert current == "lock-c"

    ok, current = wopi_store.unlock(doc["file_id"], "lock-b")
    assert ok is False
    assert current == "lock-c"

    ok, current = wopi_store.unlock(doc["file_id"], "lock-c")
    assert ok is True
    assert current == ""


def test_close_session_revokes_token_lock_and_open_document_metadata(office_state):
    doc = wopi_store.create_document("document", "Close Test", "docx", "")
    session = wopi_store.create_session(doc["file_id"], "user-a", "write", "http://localhost:32080")
    ok, current = wopi_store.lock(doc["file_id"], "close-lock", session["session_id"], 120)
    assert ok is True
    assert current == "close-lock"

    open_docs = wopi_store.get_open_documents()
    assert len(open_docs) == 1
    assert open_docs[0]["file_id"] == doc["file_id"]
    assert open_docs[0]["open_sessions"] == 1

    assert wopi_store.close_session(session_id=session["session_id"]) == 1
    assert wopi_store.get_open_documents() == []
    assert wopi_store.get_lock(doc["file_id"]) == ""
    with pytest.raises(PermissionError):
        wopi_store.validate_token(session["access_token"], doc["file_id"])
    assert wopi_store.close_session(session_id=session["session_id"]) == 0


def test_sync_open_sessions_closes_sessions_without_visible_tabs(office_state):
    first = wopi_store.create_document("document", "Visible", "docx", "shown")
    second = wopi_store.create_document("document", "Orphan", "docx", "hidden")
    visible = wopi_store.create_session(first["file_id"], "user-a", "write", "http://localhost:32080")
    orphan = wopi_store.create_session(second["file_id"], "user-a", "write", "http://localhost:32080")
    ok, _ = wopi_store.lock(second["file_id"], "orphan-lock", orphan["session_id"], 120)
    assert ok is True
    with wopi_store.connect() as conn:
        conn.execute(
            "UPDATE sessions SET created_at = ? WHERE session_id = ?",
            (wopi_store.now() - wopi_store.ORPHAN_SESSION_GRACE_SECONDS - 1, orphan["session_id"]),
        )

    assert wopi_store.sync_open_sessions([visible["session_id"]]) == 1

    open_docs = wopi_store.get_open_documents()
    assert len(open_docs) == 1
    assert open_docs[0]["file_id"] == first["file_id"]
    assert wopi_store.get_lock(second["file_id"]) == ""
    with pytest.raises(PermissionError):
        wopi_store.validate_token(orphan["access_token"], second["file_id"])


def test_sync_open_sessions_preserves_new_sessions_during_mount_race(office_state):
    doc = wopi_store.create_document("document", "Fresh", "docx", "new")
    session = wopi_store.create_session(doc["file_id"], "user-a", "write", "http://localhost:32080")

    assert wopi_store.sync_open_sessions([]) == 0

    token_info = wopi_store.validate_token(session["access_token"], doc["file_id"], require_write=True)
    assert token_info["session"]["session_id"] == session["session_id"]


def test_recent_documents_include_lightweight_previews(office_state):
    doc = wopi_store.create_document("document", "Preview Memo", "docx", "A calm dashboard.")
    sheet = wopi_store.create_document("spreadsheet", "Preview Sheet", "xlsx", "Name,Value\nOffice,1")
    deck = wopi_store.create_document("presentation", "Preview Deck", "pptx", "First slide")

    previews = {
        item["file_id"]: item["preview"]
        for item in wopi_store.get_recent_documents(limit=3)
    }

    assert previews[doc["file_id"]]["lines"][0] == "Preview Memo"
    assert previews[sheet["file_id"]]["rows"][0] == ["Name", "Value"]
    assert previews[deck["file_id"]]["slides"][0]["title"] == "Preview Deck"


def test_put_file_requires_lock_and_updates_version_history(office_state):
    doc = wopi_store.create_document("document", "Save Test", "docx", "before")
    session = wopi_store.create_session(doc["file_id"], "user-a", "write", "http://localhost:32080")

    with pytest.raises(wopi_store.LockMismatch):
        wopi_store.put_file(doc["file_id"], b"after", "")

    ok, _ = wopi_store.lock(doc["file_id"], "save-lock", session["session_id"], 120)
    assert ok is True
    next_version = wopi_store.put_file(doc["file_id"], b"after", "save-lock")
    saved = wopi_store.get_document(doc["file_id"])

    assert next_version == wopi_store.item_version(saved)
    assert saved["size"] == len(b"after")
    assert (office_state["documents"] / "Save Test.docx").read_bytes() == b"after"
    assert wopi_store.version_history(doc["file_id"])


def test_wopi_routes_return_conflict_lock_header(office_state):
    app = Flask(__name__)
    wopi_routes.register_wopi_routes(app)
    doc = wopi_store.create_document("document", "Route Test", "docx", "")
    session = wopi_store.create_session(doc["file_id"], "user-a", "write", "http://localhost:32080")

    with app.test_client() as client:
        first = client.post(
            f"/wopi/files/{doc['file_id']}?access_token={session['access_token']}",
            headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "route-lock"},
        )
        assert first.status_code == 200

        conflict = client.post(
            f"/wopi/files/{doc['file_id']}?access_token={session['access_token']}",
            headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "other-lock"},
        )
        assert conflict.status_code == 409
        assert conflict.headers["X-WOPI-Lock"] == "route-lock"


def test_office_proxy_accepts_encoded_wopi_socket_token_without_session_cookie(office_state):
    pytest.importorskip("starlette")
    from plugins._office.helpers import office_proxy

    doc = wopi_store.create_document("document", "Socket Token", "docx", "")
    session = wopi_store.create_session(doc["file_id"], "user-a", "write", "http://127.0.0.1:32080")
    encoded_wopi = (
        f"http%3A%2F%2F127.0.0.1%3A80%2Fwopi%2Ffiles%2F{doc['file_id']}"
        f"%3Faccess_token%3D{session['access_token']}"
        f"%26access_token_ttl%3D{session['access_token_ttl']}"
    )
    scope = {
        "type": "websocket",
        "path": f"/office/cool/{encoded_wopi}/ws",
        "raw_path": f"/office/cool/{encoded_wopi}/ws".encode("latin-1"),
        "query_string": b"",
        "headers": [],
    }

    proxy = office_proxy.OfficeProxy()

    assert proxy._has_valid_wopi_token(scope) is True

    headers = proxy.websocket_headers({
        "headers": [
            (b"host", b"127.0.0.1:32080"),
            (b"origin", b"http://127.0.0.1:32080"),
            (b"user-agent", b"qa"),
            (b"sec-websocket-key", b"ignored"),
        ],
    })
    assert proxy.upstream_websocket_url(scope).startswith("ws://127.0.0.1:32080/office/cool/")
    assert all(key.lower() not in {"host", "origin", "sec-websocket-key"} for key, _ in headers)
    assert ("user-agent", "qa") in headers


def test_document_artifact_docx_edit_replaces_text_and_tracks_version(office_state):
    doc = wopi_store.create_document("document", "Edit Text", "docx", "The old phrase stays here.")

    updated, payload = artifact_editor.edit_artifact(
        doc,
        operation="replace_text",
        find="old phrase",
        replace="new phrase",
    )
    content = artifact_editor.read_artifact(updated)

    assert payload["changed"] is True
    assert payload["replacements"] == 1
    assert "new phrase" in content["text"]
    assert "old phrase" not in content["text"]
    assert int(updated["version"]) == 2
    assert wopi_store.version_history(doc["file_id"])


def test_document_artifact_xlsx_edit_sets_cells_and_appends_rows(office_state):
    doc = wopi_store.create_document("spreadsheet", "Budget", "xlsx", "Name,Amount")

    updated, payload = artifact_editor.edit_artifact(
        doc,
        operation="set_cells",
        cells={"A2": "Tools", "B2": 12500},
    )
    updated, payload = artifact_editor.edit_artifact(
        updated,
        operation="append_rows",
        rows=[["Research", 9800]],
    )
    content = artifact_editor.read_artifact(updated)
    rows = content["sheets"][0]["preview_rows"]

    assert payload["changed"] is True
    assert ["Tools", 12500] in rows
    assert ["Research", 9800] in rows


def test_document_artifact_xlsx_create_parses_csv_content_for_charting(office_state):
    doc = wopi_store.create_document(
        "spreadsheet",
        "Revenue Demo",
        "xlsx",
        "\n".join([
            "Month,Revenue,Costs",
            "Jan,120,80",
            "Feb,135,92",
            "Mar,150,96",
        ]),
    )
    content = artifact_editor.read_artifact(doc)
    rows = content["sheets"][0]["preview_rows"]

    assert rows[0] == ["Month", "Revenue", "Costs"]
    assert rows[1] == ["Jan", 120, 80]

    updated, payload = artifact_editor.edit_artifact(
        doc,
        operation="create_chart",
        chart={"type": "line", "position": "E1"},
    )

    assert payload["changed"] is True
    assert payload["charts"][0]["type"] == "line"
    assert payload["charts"][0]["position"] == "E1"
    assert artifact_editor.read_artifact(updated)["sheets"][0]["chart_count"] == 1


def test_document_artifact_xlsx_stock_chart_rejects_non_numeric_ohlc_data(office_state):
    doc = wopi_store.create_document(
        "spreadsheet",
        "Broken Trading Demo",
        "xlsx",
        "\n".join([
            "Date,Open,High,Low,Close",
            "2026-04-24,open,high,low,close",
            "2026-04-25,still,not,real,numbers",
        ]),
    )

    with pytest.raises(ValueError, match="no numeric data"):
        artifact_editor.edit_artifact(doc, operation="create_chart", chart={"type": "candlestick"})


def test_document_artifact_xlsx_edit_creates_stock_chart(office_state):
    doc = wopi_store.create_document("spreadsheet", "Trading Demo", "xlsx", "")
    rows = [
        ["Date", "Open", "High", "Low", "Close", "Volume"],
        ["2026-04-24", 100, 105, 99, 104, 1000],
        ["2026-04-25", 104, 106, 102, 103, 1200],
        ["2026-04-28", 103, 108, 101, 107, 1800],
    ]
    updated, _ = artifact_editor.edit_artifact(doc, operation="set_rows", rows=rows)

    updated, payload = artifact_editor.edit_artifact(
        updated,
        operation="create_chart",
        chart={
            "type": "candlestick",
            "title": "DEMO Stock Price (OHLC)",
            "position": "A8",
            "width": 16,
            "height": 8,
        },
    )
    content = artifact_editor.read_artifact(updated)
    sheet = content["sheets"][0]

    assert payload["changed"] is True
    assert payload["charts_created"] == 1
    assert payload["charts"][0]["type"] == "stock"
    assert payload["charts"][0]["series_count"] == 4
    assert sheet["chart_count"] == 1
    assert sheet["charts"][0]["type"] == "stock"
    assert sheet["charts"][0]["title"] == "DEMO Stock Price (OHLC)"


def test_document_artifact_pptx_edit_sets_slides(office_state):
    doc = wopi_store.create_document("presentation", "Roadmap", "pptx", "Initial")

    updated, payload = artifact_editor.edit_artifact(
        doc,
        operation="set_slides",
        slides=[
            {"title": "Vision", "bullets": ["Elegant", "Useful"]},
            {"title": "Plan", "bullets": ["Build", "Verify"]},
        ],
    )
    content = artifact_editor.read_artifact(updated)

    assert payload["changed"] is True
    assert content["slide_count"] == 2
    assert [slide["title"] for slide in content["slides"]] == ["Vision", "Plan"]


def test_office_canvas_context_lists_active_metadata_without_file_contents(office_state):
    doc = wopi_store.create_document("document", "Canvas Context", "docx", "private body text")
    wopi_store.create_session(doc["file_id"], "user-a", "write", "http://localhost:32080")

    context = canvas_context.build_context()

    assert "Canvas Context.docx" in context
    assert doc["file_id"] in context
    assert "private body text" not in context


def test_office_artifacts_skill_metadata_is_valid():
    skill_path = PROJECT_ROOT / "plugins" / "_office" / "skills" / "office-artifacts" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "\nname: office-artifacts\n" in text
    assert "description:" in text
    assert "allowed_tools:" in text
    assert "document_artifact" in text
