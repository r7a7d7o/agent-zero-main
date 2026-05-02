from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import types
import zipfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._office import hooks
from plugins._office.helpers import (
    artifact_editor,
    canvas_context,
    document_store,
    libreoffice,
    libreoffice_desktop,
    libreofficekit_native,
    libreofficekit_sessions,
    libreofficekit_worker,
)


@pytest.fixture
def office_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    backups = state / "backups"
    workdir = tmp_path / "workdir"
    documents = workdir / "documents"
    projects_parent = tmp_path / "projects"

    monkeypatch.setattr(document_store, "STATE_DIR", state)
    monkeypatch.setattr(document_store, "DB_PATH", state / "documents.sqlite3")
    monkeypatch.setattr(document_store, "BACKUP_DIR", backups)
    monkeypatch.setattr(document_store, "WORKDIR", workdir)
    monkeypatch.setattr(document_store, "DOCUMENTS_DIR", documents)
    settings_helpers = types.SimpleNamespace(get_settings=lambda: {"workdir_path": str(workdir)})
    project_helpers = types.SimpleNamespace(
        get_context_project_name=lambda context: None,
        get_project_folder=lambda name: str(projects_parent / name),
        get_projects_parent_folder=lambda: str(projects_parent),
    )
    monkeypatch.setattr(document_store, "_settings", lambda: settings_helpers)
    monkeypatch.setattr(document_store, "_projects", lambda: project_helpers)

    workdir.mkdir(parents=True, exist_ok=True)
    documents.mkdir(parents=True, exist_ok=True)
    projects_parent.mkdir(parents=True, exist_ok=True)
    document_store.ensure_dirs()
    return types.SimpleNamespace(
        state=state,
        backups=backups,
        workdir=workdir,
        documents=documents,
        projects_parent=projects_parent,
        project_helpers=project_helpers,
    )


def test_document_artifact_create_defaults_to_markdown(office_state):
    doc = document_store.create_document("document", "Research Note", content="A precise note.")

    assert doc["extension"] == "md"
    assert Path(doc["path"]).parent == office_state.workdir
    assert Path(doc["path"]).read_text(encoding="utf-8").startswith("# Research Note")


def test_explicit_docx_creates_valid_word_package(office_state):
    doc = document_store.create_document("document", "Board Memo", "docx", "A careful memo.")

    assert doc["extension"] == "docx"
    assert Path(doc["path"]).parent == office_state.documents
    assert libreoffice.validate_docx(doc["path"])["ok"] is True
    with zipfile.ZipFile(doc["path"]) as archive:
        assert "word/document.xml" in archive.namelist()


def test_blank_docx_includes_editable_body_paragraph(office_state):
    doc = document_store.create_document("document", "Blank Memo", "docx", "")
    with zipfile.ZipFile(doc["path"]) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
        root = document_store.ET.fromstring(xml)

    assert len(list(root.iter(document_store._qn(document_store.W_NS, "p")))) >= 2
    assert 'xml:space="preserve">&#160;</w:t>' in xml


def test_xlsx_and_pptx_creation_and_direct_edits_still_work(office_state):
    sheet = document_store.create_document(
        "spreadsheet",
        "Budget",
        "xlsx",
        "Name,Amount\nPlatform,1000",
    )
    updated_sheet, sheet_payload = artifact_editor.edit_artifact(
        sheet,
        operation="set_cells",
        cells={"Sheet1!B2": 12500, "Sheet1!A3": "Research", "Sheet1!B3": 4700},
    )
    sheet_read = artifact_editor.read_artifact(updated_sheet)
    rows = sheet_read["sheets"][0]["preview_rows"]

    assert sheet_payload["changed"] is True
    assert rows[1][1] == 12500
    assert rows[2][0] == "Research"

    deck = document_store.create_document(
        "presentation",
        "Roadmap",
        "pptx",
        "Roadmap\nLaunch sequence\n\n---\n\nNext\nPolish rollout",
    )
    created_deck_read = artifact_editor.read_artifact(deck)
    with zipfile.ZipFile(deck["path"]) as archive:
        created_slide_names = [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]

    assert created_deck_read["slide_count"] == 2
    assert created_deck_read["slides"][0]["title"] == "Roadmap"
    assert created_deck_read["slides"][1]["title"] == "Next"
    assert len(created_slide_names) == 2

    updated_deck, deck_payload = artifact_editor.edit_artifact(
        deck,
        operation="set_slides",
        slides=[
            {"title": "Now", "bullets": ["Stabilize"]},
            {"title": "Next", "bullets": ["Polish"]},
        ],
    )
    deck_read = artifact_editor.read_artifact(updated_deck)

    assert deck_payload["changed"] is True
    assert deck_read["slide_count"] == 2
    assert deck_read["slides"][1]["title"] == "Next"


def test_document_artifact_accepts_method_alias_for_xlsx_create(office_state, monkeypatch):
    tool_module = types.ModuleType("helpers.tool")

    class Response:
        def __init__(self, message, break_loop, additional=None):
            self.message = message
            self.break_loop = break_loop
            self.additional = additional

    class Tool:
        def __init__(self, agent, name, method, args, message, loop_data, **kwargs):
            self.agent = agent
            self.name = name
            self.method = method
            self.args = args
            self.message = message
            self.loop_data = loop_data

    tool_module.Response = Response
    tool_module.Tool = Tool
    monkeypatch.setitem(sys.modules, "helpers.tool", tool_module)
    spec = importlib.util.spec_from_file_location(
        "test_document_artifact_tool",
        PROJECT_ROOT / "plugins" / "_office" / "tools" / "document_artifact.py",
    )
    document_artifact_module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(document_artifact_module)
    DocumentArtifact = document_artifact_module.DocumentArtifact

    tool = DocumentArtifact(
        agent=None,
        name="document_artifact",
        method=None,
        args={},
        message="",
        loop_data=None,
    )

    response = asyncio.run(
        tool.execute(
            method="create",
            kind="document",
            title="New Excel Workbook",
            format="xlsx",
            content="Sheet1\n",
        )
    )
    payload = json.loads(response.message)

    assert payload["action"] == "create"
    assert payload["document"]["extension"] == "xlsx"
    assert Path(payload["document"]["path"]).name == "New Excel Workbook.xlsx"
    assert Path(document_store._path_from_a0(payload["document"]["path"])).exists()


def test_odt_is_not_advertised_and_returns_clear_unsupported_response(office_state):
    prompt = (PROJECT_ROOT / "plugins" / "_office" / "prompts" / "agent.system.tool.document_artifact.md").read_text(
        encoding="utf-8",
    )

    assert "formats: md docx xlsx pptx" in prompt
    assert "`method` is accepted as an alias for action" in prompt
    assert "they do not open the canvas automatically" in prompt
    assert "Download and Open in canvas message actions" in prompt
    with pytest.raises(ValueError, match="ODT editing is not supported"):
        document_store.create_document("document", "Skip ODT", "odt", "")


def test_project_scoped_creation_uses_active_project_root(office_state, monkeypatch):
    project_root = office_state.projects_parent / "apollo"
    project_root.mkdir(parents=True, exist_ok=True)
    context = object()
    agent_module = types.SimpleNamespace(
        AgentContext=types.SimpleNamespace(get=staticmethod(lambda context_id: context))
    )

    monkeypatch.setitem(sys.modules, "agent", agent_module)
    monkeypatch.setattr(office_state.project_helpers, "get_context_project_name", lambda active_context: "apollo")
    monkeypatch.setattr(office_state.project_helpers, "get_project_folder", lambda name: str(project_root))

    markdown = document_store.create_document("document", "Project Note", "md", "Scoped.", context_id="ctx-project")
    docx = document_store.create_document("document", "Project Memo", "docx", "Scoped.", context_id="ctx-project")

    assert Path(markdown["path"]).parent == project_root
    assert Path(docx["path"]).parent == project_root / "documents"


def test_non_project_creation_uses_configured_workdir(office_state):
    markdown = document_store.create_document("document", "Workdir Note", content="Plain.")
    spreadsheet = document_store.create_document("spreadsheet", "Workdir Sheet", "xlsx", "Name,Value")

    assert markdown["extension"] == "md"
    assert Path(markdown["path"]).parent == office_state.workdir
    assert Path(spreadsheet["path"]).parent == office_state.documents


def test_sessions_recent_preview_and_canvas_context_are_neutral(office_state):
    doc = document_store.create_document("document", "Canvas Context", "md", "Private body text.")
    session = document_store.create_session(doc["file_id"], "user-a", "write", "http://localhost:32080")

    open_docs = document_store.get_open_documents()
    recent = document_store.get_recent_documents()
    context = canvas_context.build_context()

    assert open_docs[0]["file_id"] == doc["file_id"]
    assert recent[0]["preview"]["lines"]
    assert "document artifacts" in context
    assert "Private body text" not in context
    assert document_store.close_session(session_id=session["session_id"]) == 1
    assert document_store.get_open_documents() == []


def test_markdown_save_tracks_version_history(office_state):
    doc = document_store.create_document("document", "Versioned", "md", "First")
    updated = document_store.write_markdown(doc["file_id"], "# Versioned\n\nSecond\n")
    history = document_store.version_history(doc["file_id"])

    assert updated["version"] == 2
    assert history
    assert Path(updated["path"]).read_text(encoding="utf-8").endswith("Second\n")


def test_direct_markdown_edits_refresh_open_canvas_session(office_state, monkeypatch):
    manager = libreofficekit_sessions.LibreOfficeKitSessionManager()
    monkeypatch.setattr(libreofficekit_sessions, "_manager", manager, raising=False)
    doc = document_store.create_document("document", "Receiver", "md", "First")
    session = manager.open(doc)

    artifact_editor.edit_artifact(doc, operation="set_text", content="# Receiver\n\nSecond")

    assert manager._sessions[session["session_id"]].text == "# Receiver\n\nSecond"


def test_docx_session_dispatches_native_uno_commands(office_state, monkeypatch):
    calls = []

    class FakeNativeDocument:
        def metadata(self):
            return {"available": True, "doctype": 0, "parts": 1, "width_twips": 100, "height_twips": 200}

        def post_uno_command(self, command, arguments=None, notify=True):
            calls.append((command, arguments, notify))
            return {"ok": True, "native": True, "command": command}

        def command_values(self, command):
            return {"ok": True, "native": True, "command": command, "values": {"commandName": command}}

        def close(self):
            calls.append(("close", None, None))

    monkeypatch.setattr(libreofficekit_native, "open_document", lambda path: FakeNativeDocument())

    manager = libreofficekit_sessions.LibreOfficeKitSessionManager()
    doc = document_store.create_document("document", "Native", "docx", "Native text")
    session = manager.open(doc)
    result = manager.command(session["session_id"], ".uno:Bold", notify=True)
    values = manager.command_values(session["session_id"], ".uno:StyleApply")
    manager.close(session["session_id"])

    assert session["native"]["available"] is True
    assert result["ok"] is True
    assert result["native"] is True
    assert values["values"]["commandName"] == ".uno:StyleApply"
    assert calls[0] == (".uno:Bold", None, True)
    assert calls[-1] == ("close", None, None)


def test_lok_worker_serializes_concurrent_rpc_calls():
    import concurrent.futures
    import threading
    import time

    document = object.__new__(libreofficekit_worker.WorkerLokDocument)
    document._lock = threading.RLock()
    active = 0
    max_active = 0

    def fake_request_unlocked(action, payload=None, timeout=18):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.01)
        active -= 1
        return {"ok": True, "action": action, "payload": payload}

    document._request_unlocked = fake_request_unlocked
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda index: document._request("key", {"index": index}), range(12)))

    assert all(result["ok"] is True for result in results)
    assert max_active == 1


def test_official_libreoffice_desktop_status_and_url_contract(tmp_path, monkeypatch):
    xpra_html = tmp_path / "xpra" / "www"
    xpra_html.mkdir(parents=True)
    (xpra_html / "index.html").write_text("xpra", encoding="utf-8")

    monkeypatch.setattr(libreoffice_desktop.libreoffice, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(
        libreoffice_desktop.shutil,
        "which",
        lambda name: f"/usr/bin/{name}"
        if name
        in {
            "xpra",
            "Xvfb",
            "xfce4-session",
            "dbus-launch",
            "xrandr",
            "xdotool",
            "thunar",
            "xfce4-terminal",
            "xfce4-settings-manager",
            "gio",
            "pulseaudio",
            "pactl",
        }
        else "",
    )
    monkeypatch.setattr(libreoffice_desktop.virtual_desktop, "XPRA_HTML_ROOT_CANDIDATES", (xpra_html,))
    monkeypatch.setattr(libreoffice_desktop.virtual_desktop, "_package_installed", lambda package: True)

    status = libreoffice_desktop.collect_desktop_status()
    url = libreoffice_desktop._xpra_url("abc123")

    assert status["healthy"] is True
    assert status["xpra_html_root"] == str(xpra_html)
    assert url.startswith("/desktop/session/abc123/index.html?")
    assert "path=%2Fdesktop%2Fsession%2Fabc123%2F" in url
    assert "xpramenu=false" in url
    assert "floating_menu=false" in url
    assert "file_transfer=true" in url
    assert "sound=true" in url
    assert "printing=true" in url


def test_official_libreoffice_desktop_manager_opens_binary_session(office_state, tmp_path, monkeypatch):
    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(libreoffice_desktop, "STATE_DIR", tmp_path / "desktop")
    monkeypatch.setattr(libreoffice_desktop, "SESSION_DIR", tmp_path / "desktop" / "sessions")
    monkeypatch.setattr(libreoffice_desktop, "PROFILE_DIR", tmp_path / "desktop" / "profiles")
    monkeypatch.setattr(libreoffice_desktop, "collect_desktop_status", lambda: {"healthy": True, "message": "ok"})
    monkeypatch.setattr(libreoffice_desktop.libreoffice, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(libreoffice_desktop, "_port_is_free", lambda port: True)
    monkeypatch.setattr(libreoffice_desktop.virtual_desktop, "has_window", lambda **kwargs: True)
    real_get_abs_path = libreoffice_desktop.files.get_abs_path

    def fake_get_abs_path(*parts):
        if parts and parts[0] == "usr":
            return str(tmp_path.joinpath(*parts))
        return real_get_abs_path(*parts)

    monkeypatch.setattr(libreoffice_desktop.files, "get_abs_path", fake_get_abs_path)

    def fake_spawn(self, session):
        session.profile_dir.mkdir(parents=True, exist_ok=True)
        session.processes["xpra"] = FakeProcess()

    def fake_open_document(self, session, doc):
        session.processes[f"soffice-{doc['file_id']}"] = FakeProcess()

    monkeypatch.setattr(libreoffice_desktop.LibreOfficeDesktopManager, "_spawn_desktop_locked", fake_spawn)
    monkeypatch.setattr(libreoffice_desktop.LibreOfficeDesktopManager, "_open_document_locked", fake_open_document)

    doc = document_store.create_document("spreadsheet", "Official Sheet", "xlsx", "Name,Value\nA,1")
    manager = libreoffice_desktop.LibreOfficeDesktopManager()
    payload = manager.open(doc)

    assert payload["available"] is True
    assert payload["extension"] == "xlsx"
    assert payload["url"].startswith("/desktop/session/")
    registry = tmp_path / "desktop" / "profiles" / payload["session_id"] / "user" / "registrymodifications.xcu"
    registry_text = registry.read_text(encoding="utf-8")
    assert "ooSetupInstCompleted" in registry_text
    assert "FirstRun" in registry_text
    assert "Office.Paths/Variables" in registry_text
    assert "Office.Paths:NamedPath['Work']" in registry_text
    assert office_state.workdir.as_uri() in registry_text
    writer_launcher = tmp_path / "desktop" / "profiles" / payload["session_id"] / "Desktop" / "LibreOffice Writer.desktop"
    writer_text = writer_launcher.read_text(encoding="utf-8")
    assert "--writer" in writer_text
    assert f"Path={office_state.workdir}" in writer_text
    assert "X-XFCE-Trusted=true" in writer_text
    terminal_launcher = tmp_path / "desktop" / "profiles" / payload["session_id"] / "Desktop" / "Terminal.desktop"
    files_launcher = tmp_path / "desktop" / "profiles" / payload["session_id"] / "Desktop" / "Files.desktop"
    settings_launcher = tmp_path / "desktop" / "profiles" / payload["session_id"] / "Desktop" / "Settings.desktop"
    terminal_text = terminal_launcher.read_text(encoding="utf-8")
    settings_text = settings_launcher.read_text(encoding="utf-8")
    assert "xfce4-terminal" in terminal_text
    assert "org.xfce.terminal" in terminal_text
    assert not files_launcher.exists()
    assert not (tmp_path / "desktop" / "profiles" / payload["session_id"] / "Desktop" / "Browser.desktop").exists()
    assert "xfce4-settings-manager" in settings_text
    assert "org.xfce.settings.manager" in settings_text
    link_targets = {
        "Projects": "usr/projects",
        "Skills": "usr/skills",
        "Agents": "usr/agents",
        "Downloads": "usr/downloads",
    }
    workdir_link = tmp_path / "desktop" / "profiles" / payload["session_id"] / "Desktop" / "Workdir"
    assert workdir_link.is_symlink()
    assert workdir_link.resolve() == office_state.workdir
    for link_name, target in link_targets.items():
        link = tmp_path / "desktop" / "profiles" / payload["session_id"] / "Desktop" / link_name
        assert link.is_symlink()
        assert str(link.resolve()).endswith(target)
    xpra_override = (
        tmp_path
        / "desktop"
        / "profiles"
        / payload["session_id"]
        / ".local"
        / "share"
        / "applications"
        / "xpra-gui.desktop"
    )
    assert "Hidden=true" in xpra_override.read_text(encoding="utf-8")
    desktop_profile = (
        tmp_path
        / "desktop"
        / "profiles"
        / payload["session_id"]
        / ".config"
        / "xfce4"
        / "xfconf"
        / "xfce-perchannel-xml"
        / "xfce4-desktop.xml"
    )
    desktop_profile_text = desktop_profile.read_text(encoding="utf-8")
    assert "desktop-icons" in desktop_profile_text
    assert "image-path" in desktop_profile_text
    assert "usr/downloads" in desktop_profile_text
    user_dirs = (
        tmp_path
        / "desktop"
        / "profiles"
        / payload["session_id"]
        / ".config"
        / "user-dirs.dirs"
    ).read_text(encoding="utf-8")
    assert 'XDG_PICTURES_DIR="' in user_dirs
    assert "usr/downloads" in user_dirs
    assert f'XDG_DOCUMENTS_DIR="{office_state.workdir}"' in user_dirs
    panel_profile = (
        tmp_path
        / "desktop"
        / "profiles"
        / payload["session_id"]
        / ".config"
        / "xfce4"
        / "xfconf"
        / "xfce-perchannel-xml"
        / "xfce4-panel.xml"
    ).read_text(encoding="utf-8")
    assert "panel-1" in panel_profile
    assert "panel-2" not in panel_profile
    assert "launcher" not in panel_profile
    desktop_helper = (
        PROJECT_ROOT / "plugins" / "_office" / "helpers" / "libreoffice_desktop.py"
    ).read_text(encoding="utf-8")
    assert "_refresh_xfce_desktop" in desktop_helper
    assert "DBUS_SESSION_BUS_ADDRESS" in desktop_helper
    autostart = (
        tmp_path
        / "desktop"
        / "profiles"
        / payload["session_id"]
        / ".config"
        / "autostart"
        / "agent-zero-office-desktop.desktop"
    )
    assert "prepare-xfce-profile.sh" in autostart.read_text(encoding="utf-8")
    profile_script = (
        tmp_path
        / "desktop"
        / "profiles"
        / payload["session_id"]
        / "prepare-xfce-profile.sh"
    ).read_text(encoding="utf-8")
    assert '"$HOME"/Desktop/*.desktop' in profile_script
    assert "agent-zero-settings.desktop" not in profile_script
    assert "metadata::xfce-exe-checksum" in profile_script
    assert manager.proxy_for_token(payload["token"]) == ("127.0.0.1", libreoffice_desktop.XPRA_PORT_BASE)
    assert manager.close(payload["session_id"], save_first=False)["closed"] == 0
    assert manager.close(payload["session_id"], save_first=False)["persistent"] is True


def test_libreoffice_desktop_cleanup_preserves_live_owner_manifest(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    manifest = session_dir / "live.json"
    manifest.write_text(
        json.dumps({"owner_pid": os.getpid(), "pids": {"xpra": 987654}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(libreoffice_desktop, "SESSION_DIR", session_dir)
    monkeypatch.setattr(
        libreoffice_desktop,
        "_kill_pid",
        lambda _pid: pytest.fail("cleanup should not kill a desktop owned by a live UI process"),
    )

    result = libreoffice_desktop.cleanup_stale_runtime_state()

    assert result["killed"] == []
    assert manifest.exists()


def test_libreoffice_desktop_removes_stale_lock_file(tmp_path):
    doc_path = tmp_path / "Deck.pptx"
    doc_path.write_text("pptx", encoding="utf-8")
    lock_path = tmp_path / ".~lock.Deck.pptx#"
    lock_path.write_text("stale", encoding="utf-8")
    session = libreoffice_desktop.DesktopSession(
        session_id="session",
        file_id="file",
        extension="pptx",
        path=str(doc_path),
        title=doc_path.name,
        display=libreoffice_desktop.DISPLAY_BASE,
        xpra_port=libreoffice_desktop.XPRA_PORT_BASE,
        token="token",
        url="/desktop/session/token/index.html",
        profile_dir=tmp_path / "profile",
    )

    libreoffice_desktop.LibreOfficeDesktopManager()._remove_stale_lock_file(session)

    assert not lock_path.exists()


def test_cleanup_hook_removes_stale_runtime_state_idempotently(tmp_path, monkeypatch):
    source = tmp_path / "sources.list.d" / "retired.sources"
    keyring = tmp_path / "keyrings" / "retired.gpg"
    supervisor = tmp_path / "supervisor" / "retired.conf"
    runtime_dir = tmp_path / "runtime"
    marker = tmp_path / "state" / "cleanup.done"

    for path in (source, keyring, supervisor):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("old\n", encoding="utf-8")
    (runtime_dir / "nested").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "nested" / "state.txt").write_text("old\n", encoding="utf-8")

    monkeypatch.setattr(hooks, "APT_SOURCE_FILE", source)
    monkeypatch.setattr(hooks, "APT_KEYRING_FILE", keyring)
    monkeypatch.setattr(hooks, "SUPERVISOR_FILE", supervisor)
    monkeypatch.setattr(hooks, "RUNTIME_DIRS", [runtime_dir])
    monkeypatch.setattr(hooks, "CLEANUP_MARKER", marker)
    monkeypatch.setattr(hooks, "_installed_packages", lambda packages: [])
    monkeypatch.setattr(hooks, "_kill_old_processes", lambda errors: None)

    def fake_ensure(installed, errors):
        assert not source.exists()
        installed.append("xpra")

    def fake_purge(removed, errors, **kwargs):
        return None

    monkeypatch.setattr(hooks, "_ensure_runtime_dependencies", fake_ensure)
    monkeypatch.setattr(hooks, "_purge_packages", fake_purge)

    first = hooks.cleanup_stale_runtime_state(force=True)
    second = hooks.cleanup_stale_runtime_state(force=True)
    skipped = hooks.cleanup_stale_runtime_state()

    assert first["ok"] is True
    assert first["installed"] == ["xpra"]
    assert second["ok"] is True
    assert skipped["skipped"] is True
    assert not source.exists()
    assert not keyring.exists()
    assert not supervisor.exists()
    assert not runtime_dir.exists()
    assert marker.exists()


def test_office_startup_bootstraps_persistent_desktop_runtime(monkeypatch):
    calls = []
    routes_module = types.ModuleType("plugins._office.helpers.libreoffice_desktop_routes")
    routes_module.install_route_hooks = lambda: calls.append("routes")
    monkeypatch.setitem(sys.modules, "plugins._office.helpers.libreoffice_desktop_routes", routes_module)
    monkeypatch.delitem(
        sys.modules,
        "plugins._office.extensions.python.startup_migration._20_office_routes",
        raising=False,
    )

    from plugins._office.extensions.python.startup_migration import _20_office_routes as office_startup

    class Manager:
        def ensure_system_desktop(self):
            calls.append("desktop")
            return {"available": True, "session_id": "agent-zero-desktop"}

    monkeypatch.setattr(
        office_startup.hooks,
        "cleanup_stale_runtime_state",
        lambda: {"ok": True, "errors": [], "installed": [], "removed": []},
    )
    monkeypatch.setattr(
        office_startup.libreoffice_desktop,
        "get_manager",
        lambda: Manager(),
    )

    office_startup.OfficeStartupCleanup(agent=None).execute()

    assert calls == ["routes", "desktop"]


def test_cleanup_hook_reruns_when_stale_packages_exist_after_old_marker(tmp_path, monkeypatch):
    marker = tmp_path / "state" / "cleanup.done"
    marker.parent.mkdir(parents=True)
    marker.write_text("old\n", encoding="utf-8")

    monkeypatch.setattr(hooks, "APT_SOURCE_FILE", tmp_path / "missing.sources")
    monkeypatch.setattr(hooks, "APT_KEYRING_FILE", tmp_path / "missing.gpg")
    monkeypatch.setattr(hooks, "SUPERVISOR_FILE", tmp_path / "missing.conf")
    monkeypatch.setattr(hooks, "RUNTIME_DIRS", [])
    monkeypatch.setattr(hooks, "CLEANUP_MARKER", marker)
    monkeypatch.setattr(hooks, "_installed_packages", lambda packages: ["coolwsd"])
    monkeypatch.setattr(hooks, "_ensure_runtime_dependencies", lambda installed, errors: None)
    monkeypatch.setattr(hooks, "_kill_old_processes", lambda errors: None)

    def fake_purge(removed, errors, **kwargs):
        removed.extend(kwargs["installed_packages"])

    monkeypatch.setattr(hooks, "_purge_packages", fake_purge)

    result = hooks.cleanup_stale_runtime_state()

    assert result["skipped"] is False
    assert result["removed"] == ["coolwsd"]


def test_cleanup_hook_installs_missing_libreoffice_desktop_dependencies(monkeypatch):
    calls = []
    installed_state = {"xpra": False}

    monkeypatch.setattr(hooks.os, "geteuid", lambda: 0)
    monkeypatch.setattr(hooks.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"apt-get", "dpkg-query"} else "")
    monkeypatch.setattr(hooks, "RUNTIME_PACKAGES", ("xpra",))
    monkeypatch.setattr(hooks, "_package_installed", lambda package: installed_state.get(package, False))

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["apt-get", "install"]:
            installed_state["xpra"] = True
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hooks.subprocess, "run", fake_run)
    installed = []
    errors = []

    hooks._ensure_runtime_dependencies(installed, errors)

    assert installed == ["xpra"]
    assert errors == []
    assert calls[0] == ["apt-get", "update"]
    assert calls[1][:4] == ["apt-get", "install", "-y", "--no-install-recommends"]


def test_cleanup_hook_enables_official_xpra_repo_when_kali_lacks_candidate(tmp_path, monkeypatch):
    calls = []
    installed_state = {"xpra": False, "ca-certificates": True}
    keyring = tmp_path / "keyrings" / "xpra.asc"
    source = tmp_path / "sources.list.d" / "xpra.sources"

    monkeypatch.setattr(hooks.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        hooks.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"apt-get", "dpkg-query", "apt-cache"} else "",
    )
    monkeypatch.setattr(hooks, "RUNTIME_PACKAGES", ("xpra",))
    monkeypatch.setattr(hooks, "XPRA_KEYRING_FILE", keyring)
    monkeypatch.setattr(hooks, "XPRA_SOURCE_FILE", source)
    monkeypatch.setattr(hooks, "_download", lambda url: b"xpra-key")
    monkeypatch.setattr(hooks, "_read_os_release", lambda: {"ID": "kali", "VERSION_CODENAME": "kali-rolling"})
    monkeypatch.setattr(hooks, "_dpkg_architecture", lambda: "amd64")
    monkeypatch.setattr(hooks, "_package_installed", lambda package: installed_state.get(package, False))

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["apt-cache", "policy"]:
            return types.SimpleNamespace(returncode=0, stdout="Candidate: (none)\n", stderr="")
        if command[:2] == ["apt-get", "install"]:
            installed_state["xpra"] = True
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hooks.subprocess, "run", fake_run)
    installed = []
    errors = []

    hooks._ensure_runtime_dependencies(installed, errors)

    assert errors == []
    assert installed == ["xpra"]
    assert keyring.read_bytes() == b"xpra-key"
    assert "URIs: https://xpra.org/beta" in source.read_text(encoding="utf-8")
    assert "Suites: sid" in source.read_text(encoding="utf-8")
    assert calls.count(["apt-get", "update"]) == 2
    assert calls[-1][:4] == ["apt-get", "install", "-y", "--no-install-recommends"]


def test_self_update_launch_invokes_office_cleanup(monkeypatch, tmp_path):
    manager = load_self_update_manager()
    calls = []

    class Logger:
        def log(self, message=""):
            return None

    class Process:
        pass

    monkeypatch.setattr(manager, "run_office_cleanup_hook", lambda repo_dir, logger: calls.append(repo_dir))
    monkeypatch.setattr(manager, "run_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *args, **kwargs: Process())

    repo = tmp_path / "repo"
    repo.mkdir()
    process = manager.launch_ui_process(repo, Logger())

    assert isinstance(process, Process)
    assert calls == [repo]


def load_self_update_manager():
    manager_path = PROJECT_ROOT / "docker" / "run" / "fs" / "exe" / "self_update_manager.py"
    spec = importlib.util.spec_from_file_location("test_self_update_manager_office", manager_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
