from __future__ import annotations

import base64
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


REQUEST_TIMEOUT_SECONDS = 18


def open_document(path: str | Path) -> "WorkerLokDocument":
    return WorkerLokDocument(path)


class WorkerLokDocument:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._counter = 0
        self._lock = threading.RLock()
        self._process = subprocess.Popen(
            [sys.executable, "-m", "plugins._office.helpers.libreofficekit_worker", "--worker"],
            cwd=str(Path(__file__).resolve().parents[3]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "SAL_USE_VCLPLUGIN": os.environ.get("SAL_USE_VCLPLUGIN", "gen")},
        )
        opened = self._request("open", {"path": str(self.path)}, timeout=REQUEST_TIMEOUT_SECONDS)
        if not opened.get("ok"):
            raise RuntimeError(opened.get("error") or "LibreOfficeKit worker could not open document.")
        self._metadata = opened.get("metadata") or {}

    def metadata(self) -> dict[str, Any]:
        response = self._request("metadata")
        if response.get("metadata"):
            self._metadata = response["metadata"]
        return dict(self._metadata)

    def render_tiles(self, pixel_width: int = 920, max_tiles: int = 12) -> list[dict[str, Any]]:
        response = self._request("tiles", {"pixel_width": pixel_width, "max_tiles": max_tiles}, timeout=REQUEST_TIMEOUT_SECONDS)
        return response.get("tiles") or []

    def post_uno_command(self, command: str, arguments: dict[str, Any] | str | None = None, notify: bool = True) -> dict[str, Any]:
        return self._request("command", {"command": command, "arguments": arguments, "notify": notify})

    def command_values(self, command: str) -> dict[str, Any]:
        return self._request("command_values", {"command": command})

    def post_mouse_event(
        self,
        kind: str,
        x: int,
        y: int,
        count: int = 1,
        buttons: int = 1,
        modifier: int = 0,
    ) -> dict[str, Any]:
        return self._request("mouse", {
            "type": kind,
            "x": x,
            "y": y,
            "count": count,
            "buttons": buttons,
            "modifier": modifier,
        })

    def post_key_event(self, kind: str, char_code: int = 0, key_code: int = 0) -> dict[str, Any]:
        return self._request("key", {
            "type": kind,
            "char_code": char_code,
            "key_code": key_code,
        })

    def type_text(self, text: str) -> dict[str, Any]:
        return self._request("text", {"text": text})

    def save_to_bytes(self, suffix: str = ".docx", fmt: str | None = "docx") -> bytes:
        response = self._request("save", {"suffix": suffix, "format": fmt}, timeout=REQUEST_TIMEOUT_SECONDS)
        data = response.get("bytes") or ""
        return base64.b64decode(data.encode("ascii"))

    def close(self) -> None:
        process = self._process
        if process.poll() is not None:
            return
        try:
            self._request("close", timeout=3)
            process.wait(timeout=3)
        except Exception:
            process.kill()
            process.wait(timeout=3)

    def _request(self, action: str, payload: dict[str, Any] | None = None, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
        with self._lock:
            return self._request_unlocked(action, payload=payload, timeout=timeout)

    def _request_unlocked(self, action: str, payload: dict[str, Any] | None = None, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
        process = self._process
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"LibreOfficeKit worker exited with {process.returncode}: {stderr.strip()}")
        self._counter += 1
        message = {"id": self._counter, "action": action, **(payload or {})}
        assert process.stdin is not None
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()
        assert process.stdout is not None
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], max(0.05, min(0.5, deadline - time.time())))
            if not ready:
                continue
            line = process.stdout.readline()
            if not line:
                break
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") == self._counter:
                if response.get("ok") is False:
                    raise RuntimeError(response.get("error") or f"LibreOfficeKit worker {action} failed.")
                return response
        process.kill()
        raise TimeoutError(f"LibreOfficeKit worker timed out during {action}.")


def _worker_loop() -> None:
    from plugins._office.helpers import libreofficekit_native

    document = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            action = request.get("action")
            if action == "open":
                document = libreofficekit_native.open_document_in_process(request["path"])
                _respond(request, {"ok": True, "metadata": document.metadata()})
            elif not document:
                _respond(request, {"ok": False, "error": "Document is not open."})
            elif action == "metadata":
                _respond(request, {"ok": True, "metadata": document.metadata()})
            elif action == "tiles":
                _respond(request, {
                    "ok": True,
                    "tiles": document.render_tiles(
                        pixel_width=int(request.get("pixel_width") or 920),
                        max_tiles=int(request.get("max_tiles") or 12),
                    ),
                })
            elif action == "command":
                result = document.post_uno_command(
                    str(request.get("command") or ""),
                    arguments=request.get("arguments"),
                    notify=bool(request.get("notify", True)),
                )
                _respond(request, {"ok": True, **result, "metadata": document.metadata()})
            elif action == "command_values":
                _respond(request, document.command_values(str(request.get("command") or "")))
            elif action == "mouse":
                result = document.post_mouse_event(
                    str(request.get("type") or "down"),
                    int(request.get("x") or 0),
                    int(request.get("y") or 0),
                    count=int(request.get("count") or 1),
                    buttons=int(request.get("buttons") or 1),
                    modifier=int(request.get("modifier") or 0),
                )
                _respond(request, {"ok": True, **result, "metadata": document.metadata(), "tiles": document.render_tiles()})
            elif action == "key":
                result = document.post_key_event(
                    str(request.get("type") or "down"),
                    char_code=int(request.get("char_code") or 0),
                    key_code=int(request.get("key_code") or 0),
                )
                _respond(request, {"ok": True, **result, "metadata": document.metadata(), "tiles": document.render_tiles()})
            elif action == "text":
                result = document.type_text(str(request.get("text") or ""))
                _respond(request, {"ok": True, **result, "metadata": document.metadata(), "tiles": document.render_tiles()})
            elif action == "save":
                data = document.save_to_bytes(
                    suffix=str(request.get("suffix") or ".docx"),
                    fmt=request.get("format") or "docx",
                )
                _respond(request, {"ok": True, "bytes": base64.b64encode(data).decode("ascii"), "metadata": document.metadata()})
            elif action == "close":
                if document:
                    document.close()
                _respond(request, {"ok": True, "closed": True})
                sys.stdout.flush()
                os._exit(0)
            else:
                _respond(request, {"ok": False, "error": f"Unknown worker action: {action}"})
        except Exception as exc:
            _respond(json.loads(line) if line.strip().startswith("{") else {}, {"ok": False, "error": str(exc)})
    os._exit(0)


def _respond(request: dict[str, Any], response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"id": request.get("id"), **response}, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__" and "--worker" in sys.argv:
    _worker_loop()
