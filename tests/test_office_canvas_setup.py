from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_office_setup_canvas_uses_simple_progress_instead_of_install_logs():
    panel = (PROJECT_ROOT / "plugins" / "_office" / "webui" / "office-panel.html").read_text(
        encoding="utf-8",
    )
    store = (PROJECT_ROOT / "plugins" / "_office" / "webui" / "office-store.js").read_text(
        encoding="utf-8",
    )

    assert "Agent Zero Office" in panel
    assert "setupTitle()" in panel
    assert "Setup in progress" in store
    assert "office-log" not in panel
    assert "collabora_logs" not in store


def test_office_dashboard_uses_cards_and_visible_tabs_for_open_files():
    panel = (PROJECT_ROOT / "plugins" / "_office" / "webui" / "office-panel.html").read_text(
        encoding="utf-8",
    )
    store = (PROJECT_ROOT / "plugins" / "_office" / "webui" / "office-store.js").read_text(
        encoding="utf-8",
    )

    assert "office-card-grid" in panel
    assert "office-document-card" in panel
    assert "openCards()" in panel
    assert "recentCards()" in panel
    assert "office-recent-row" not in panel
    assert "sync_open_sessions" in store


def test_right_canvas_keeps_restored_office_surface_until_registration_finishes():
    canvas_store = (
        PROJECT_ROOT / "webui" / "components" / "canvas" / "right-canvas-store.js"
    ).read_text(encoding="utf-8")

    init_registration = canvas_store.index('await callJsExtensions("right_canvas_register_surfaces", this);')
    init_ensure = canvas_store.index("this.ensureActiveSurface();", init_registration)
    register_surface = canvas_store.index("registerSurface(surface)")
    register_guard = canvas_store.index("if (!this._registering)", register_surface)
    guarded_ensure = canvas_store.index("this.ensureActiveSurface();", register_guard)
    open_surface = canvas_store.index("async open", register_surface)

    assert init_registration < init_ensure
    assert register_surface < register_guard < guarded_ensure < open_surface
