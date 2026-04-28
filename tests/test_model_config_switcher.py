from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_model_switcher_surfaces_default_active_models():
    switcher = (
        PROJECT_ROOT / "plugins" / "_model_config" / "webui" / "switcher-mixin.js"
    ).read_text(encoding="utf-8")
    store = (
        PROJECT_ROOT / "plugins" / "_model_config" / "webui" / "model-config-store.js"
    ).read_text(encoding="utf-8")
    template = (
        PROJECT_ROOT
        / "plugins"
        / "_model_config"
        / "extensions"
        / "webui"
        / "chat-input-progress-start"
        / "model-switcher.html"
    ).read_text(encoding="utf-8")
    api = (
        PROJECT_ROOT / "plugins" / "_model_config" / "api" / "model_override.py"
    ).read_text(encoding="utf-8")

    assert '"active_models": _active_models(ctx)' in api
    assert "switcherActiveModels" in switcher
    assert "hasActiveModelNames()" in template
    assert "Main:" in template
    assert "Utility:" in template
    assert "@media (max-width: 760px)" in template
    assert "await this.refreshActiveChatModels();" in store
    assert 'window.Alpine?.store("chats")?.selected' in store
