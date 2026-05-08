from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._a0_connector.helpers import ws_runtime
from plugins._browser.helpers.connector_runtime import (
    ConnectorBrowserRuntime,
    _agent_uses_local_chat_model,
)


def _agent(context_id: str = "ctx-host"):
    return SimpleNamespace(context=SimpleNamespace(id=context_id))


def test_host_browser_metadata_selection_is_context_scoped():
    sid = "sid-host-browser"
    context_id = "ctx-host-browser"
    ws_runtime.register_sid(sid)
    ws_runtime.subscribe_sid_to_context(sid, context_id)
    try:
        ws_runtime.store_sid_host_browser_metadata(
            sid,
            {
                "supported": True,
                "enabled": True,
                "status": "ready",
                "browser_family": "chrome",
                "profile_label": "Default",
                "content_helper_sha256": "abc123",
                "features": ["open", "content"],
            },
        )

        assert ws_runtime.select_host_browser_target_sid(context_id) == sid
        rows = ws_runtime.host_browser_metadata_for_context(context_id)
        assert rows[0]["browser_family"] == "chrome"
        assert rows[0]["enabled"] is True
        assert rows[0]["content_helper_sha256"] == "abc123"
    finally:
        ws_runtime.unregister_sid(sid)


def test_host_browser_candidate_selection_allows_disabled_supported_cli():
    sid = "sid-host-browser-disabled"
    context_id = "ctx-host-browser-disabled"
    ws_runtime.register_sid(sid)
    ws_runtime.subscribe_sid_to_context(sid, context_id)
    try:
        ws_runtime.store_sid_host_browser_metadata(
            sid,
            {
                "supported": True,
                "enabled": False,
                "status": "disabled",
                "browser_family": "chrome-a0",
                "profile_label": "Default",
                "features": ["ensure", "open"],
            },
        )

        assert ws_runtime.select_host_browser_target_sid(context_id) is None
        assert ws_runtime.select_host_browser_candidate_sid(context_id) == sid
    finally:
        ws_runtime.unregister_sid(sid)


def test_host_browser_candidate_selection_allows_preparable_cli():
    sid = "sid-host-browser-preparable"
    context_id = "ctx-host-browser-preparable"
    ws_runtime.register_sid(sid)
    ws_runtime.subscribe_sid_to_context(sid, context_id)
    try:
        ws_runtime.store_sid_host_browser_metadata(
            sid,
            {
                "supported": False,
                "can_prepare": True,
                "enabled": False,
                "status": "unsupported",
                "browser_family": "chrome-a0",
                "profile_label": "Default",
                "features": ["ensure", "open"],
                "support_reason": "Python Playwright is not installed.",
            },
        )

        assert ws_runtime.select_host_browser_target_sid(context_id) is None
        assert ws_runtime.select_host_browser_candidate_sid(context_id) == sid
        rows = ws_runtime.host_browser_metadata_for_context(context_id)
        assert rows[0]["can_prepare"] is True
    finally:
        ws_runtime.unregister_sid(sid)


def test_host_browser_metadata_infers_preparable_legacy_cli():
    sid = "sid-host-browser-legacy-preparable"
    context_id = "ctx-host-browser-legacy-preparable"
    ws_runtime.register_sid(sid)
    ws_runtime.subscribe_sid_to_context(sid, context_id)
    try:
        ws_runtime.store_sid_host_browser_metadata(
            sid,
            {
                "supported": False,
                "enabled": False,
                "status": "unsupported",
                "browser_family": "chrome-a0",
                "profile_label": "Default",
                "features": ["ensure", "open"],
                "support_reason": "Python Playwright is not installed.",
            },
        )

        rows = ws_runtime.host_browser_metadata_for_context(context_id)
        assert rows[0]["can_prepare"] is True
        assert ws_runtime.select_host_browser_candidate_sid(context_id) == sid
    finally:
        ws_runtime.unregister_sid(sid)


def test_pending_browser_op_resolves_and_disconnect_fails():
    async def run() -> None:
        sid = "sid-browser-pending"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        ws_runtime.store_pending_browser_op(
            "op-browser",
            sid=sid,
            future=future,
            loop=loop,
            context_id="ctx",
        )

        assert ws_runtime.resolve_pending_browser_op(
            "op-browser",
            sid=sid,
            payload={"op_id": "op-browser", "ok": True, "result": {"id": 1}},
        )
        assert await future == {"op_id": "op-browser", "ok": True, "result": {"id": 1}}

        future2: asyncio.Future[dict[str, object]] = loop.create_future()
        ws_runtime.store_pending_browser_op(
            "op-browser-2",
            sid=sid,
            future=future2,
            loop=loop,
            context_id="ctx",
        )
        ws_runtime.fail_pending_browser_ops_for_sid(sid, error="gone")
        assert await future2 == {"op_id": "op-browser-2", "ok": False, "error": "gone"}

    asyncio.run(run())


def test_host_browser_privacy_detects_local_model(monkeypatch):
    from plugins._model_config.helpers import model_config

    monkeypatch.setattr(
        model_config,
        "get_chat_model_config",
        lambda agent=None: {"provider": "openai", "name": "local", "api_base": "http://127.0.0.1:11434/v1"},
    )

    assert _agent_uses_local_chat_model(_agent()) is True


def test_host_browser_privacy_blocks_cloud_content(monkeypatch):
    import plugins._browser.helpers.connector_runtime as connector_runtime_module
    from plugins._model_config.helpers import model_config

    monkeypatch.setattr(
        model_config,
        "get_chat_model_config",
        lambda agent=None: {"provider": "openrouter", "name": "cloud/model", "api_base": ""},
    )
    monkeypatch.setattr(
        connector_runtime_module,
        "get_browser_config",
        lambda agent=None: {
            "host_browser_privacy_policy": "enforce_local",
        },
    )
    runtime = ConnectorBrowserRuntime("ctx-host", _agent("ctx-host"))

    with pytest.raises(RuntimeError, match="blocked by Browser privacy policy"):
        runtime._enforce_privacy({"action": "content"})


def test_connector_runtime_normalizes_host_navigation_payloads():
    runtime = ConnectorBrowserRuntime("ctx-host", _agent("ctx-host"))

    open_payload = runtime._payload_for_call("open", "localhost:3000")
    empty_open_payload = runtime._payload_for_call("open", "")
    navigate_payload = runtime._payload_for_call("navigate", 7, "novinky.cz")
    multi_payload = runtime._payload_for_call(
        "multi",
        [
            {"action": "open", "url": "example.com"},
            {"action": "navigate", "browser_id": 1, "url": "127.0.0.1:8000/path"},
            {
                "action": "multi",
                "calls": [{"action": "open", "url": "nested.example"}],
            },
            {"action": "content", "browser_id": 1},
        ],
    )

    assert open_payload["url"] == "http://localhost:3000/"
    assert empty_open_payload["url"] == ""
    assert navigate_payload["url"] == "https://novinky.cz/"
    assert multi_payload["calls"][0]["url"] == "https://example.com/"
    assert multi_payload["calls"][1]["url"] == "http://127.0.0.1:8000/path"
    assert multi_payload["calls"][2]["calls"][0]["url"] == "https://nested.example/"
    assert multi_payload["calls"][3] == {"action": "content", "browser_id": 1}


def test_host_browser_artifacts_materialize_inside_multi_results(monkeypatch, tmp_path):
    import plugins._browser.helpers.connector_runtime as connector_runtime_module

    monkeypatch.setattr(
        connector_runtime_module.files,
        "get_abs_path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    monkeypatch.setattr(
        connector_runtime_module.files,
        "normalize_a0_path",
        lambda path: "/a0/" + str(path).lstrip("/"),
    )
    runtime = ConnectorBrowserRuntime("ctx-host", _agent("ctx-host"))

    result = runtime._materialize_artifact(
        [
            {
                "ok": True,
                "result": {
                    "browser_id": 1,
                    "artifact": {
                        "filename": "shot.jpg",
                        "mime": "image/jpeg",
                        "encoding": "base64",
                        "data": "ZmFrZQ==",
                    },
                },
            }
        ]
    )

    inner = result[0]["result"]
    assert "artifact" not in inner
    assert inner["path"].endswith("shot.jpg")
    assert Path(inner["path"]).read_bytes() == b"fake"
    assert inner["vision_load"]["tool_args"]["paths"] == [inner["path"]]


def test_host_browser_artifact_materialization_rejects_oversized_payload(monkeypatch, tmp_path):
    import plugins._browser.helpers.connector_runtime as connector_runtime_module

    monkeypatch.setattr(
        connector_runtime_module.files,
        "get_abs_path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    monkeypatch.setattr(connector_runtime_module, "MAX_ARTIFACT_SIZE_BYTES", 2)
    runtime = ConnectorBrowserRuntime("ctx-host", _agent("ctx-host"))

    with pytest.raises(RuntimeError, match="too large"):
        runtime._materialize_artifact(
            {
                "artifact": {
                    "filename": "shot.jpg",
                    "mime": "image/jpeg",
                    "encoding": "base64",
                    "data": "ZmFrZQ==",
                },
            }
        )

    assert not list(tmp_path.rglob("shot.jpg"))


def test_connector_runtime_ensures_preparable_host_browser_before_action(monkeypatch):
    async def run() -> None:
        import plugins._browser.helpers.connector_runtime as connector_runtime_module

        sid = "sid-host-browser-ensure"
        context_id = "ctx-host-browser-ensure"
        emitted: list[dict[str, object]] = []

        class FakeWsManager:
            async def emit_to(self, namespace, target_sid, event, payload, handler_id=""):
                del namespace, event, handler_id
                emitted.append(dict(payload))
                assert target_sid == sid
                if payload["action"] == "ensure":
                    ws_runtime.store_sid_host_browser_metadata(
                        sid,
                        {
                            "supported": True,
                            "enabled": True,
                            "status": "active",
                            "browser_family": "chrome-a0",
                            "profile_label": "Default",
                            "features": ["ensure", "open"],
                        },
                    )
                    response = {"op_id": payload["op_id"], "ok": True, "result": {"status": "active"}}
                else:
                    response = {
                        "op_id": payload["op_id"],
                        "ok": True,
                        "result": {"id": 1, "state": {"runtime": "host"}},
                    }
                ws_runtime.resolve_pending_browser_op(payload["op_id"], sid=target_sid, payload=response)

        monkeypatch.setattr(connector_runtime_module, "get_shared_ws_manager", lambda: FakeWsManager())
        monkeypatch.setattr(
            connector_runtime_module,
            "get_browser_config",
            lambda agent=None: {"host_browser_privacy_policy": "allow"},
        )
        ws_runtime.register_sid(sid)
        ws_runtime.subscribe_sid_to_context(sid, context_id)
        try:
            ws_runtime.store_sid_host_browser_metadata(
                sid,
                {
                    "supported": False,
                    "can_prepare": True,
                    "enabled": False,
                    "status": "unsupported",
                    "browser_family": "chrome-a0",
                    "profile_label": "Default",
                    "features": ["ensure", "open"],
                    "support_reason": "Python Playwright is not installed.",
                },
            )
            runtime = ConnectorBrowserRuntime(context_id, _agent(context_id))

            result = await runtime._dispatch(
                {"op_id": "op-open", "context_id": context_id, "action": "open", "url": "https://example.com"}
            )

            assert result == {"id": 1, "state": {"runtime": "host"}}
            assert [payload["action"] for payload in emitted] == ["ensure", "open"]
            assert "__spaceBrowserPageContent__" in emitted[0]["content_helper"]["source"]
            assert "capture" in emitted[0]["content_helper"]["required_apis"]
            assert emitted[0]["content_helper"]["sha256"]
        finally:
            ws_runtime.unregister_sid(sid)

    asyncio.run(run())
