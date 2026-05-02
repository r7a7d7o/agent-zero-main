from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugins._office.helpers import document_store, libreoffice, libreofficekit_native


@dataclass
class EditorSession:
    session_id: str
    file_id: str
    sid: str
    extension: str
    path: str
    title: str
    text: str = ""
    native_document: Any | None = None
    native_metadata: dict[str, Any] = field(default_factory=dict)
    native_error: str = ""
    cursor: dict[str, Any] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    opened_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class LibreOfficeKitSessionManager:
    """Small session facade for the right canvas.

    The public contract is shaped around LibreOfficeKit-style events: open,
    input, cursor/selection, invalidated tiles, save, and close. When the native
    Python LOK bridge is available the rendering path can be swapped underneath
    this manager without changing the browser or tool APIs.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, EditorSession] = {}

    def open(self, doc: dict[str, Any], sid: str = "") -> dict[str, Any]:
        ext = str(doc["extension"]).lower()
        session_id = uuid.uuid4().hex
        text = ""
        if ext in {"md", "docx"}:
            text = document_store.read_text_for_editor(doc)
        native_document = None
        native_metadata: dict[str, Any] = {}
        native_error = ""
        if ext == "docx":
            try:
                native_document = libreofficekit_native.open_document(doc["path"])
                native_metadata = native_document.metadata()
            except Exception as exc:
                native_error = str(exc)

        session = EditorSession(
            session_id=session_id,
            file_id=doc["file_id"],
            sid=sid,
            extension=ext,
            path=doc["path"],
            title=doc["basename"],
            text=text,
            native_document=native_document,
            native_metadata=native_metadata,
            native_error=native_error,
        )
        self._sessions[session_id] = session
        return self._payload(session, doc)

    def input(self, session_id: str, text: str | None = None, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        session = self._require(session_id)
        if text is not None:
            session.text = str(text)
        elif patch:
            session.text = _apply_text_patch(session.text, patch)
        session.updated_at = time.time()
        return {"ok": True, "session_id": session_id, "invalidated_tiles": self.tiles(session_id)}

    def key(self, session_id: str, key: dict[str, Any]) -> dict[str, Any]:
        session = self._require(session_id)
        native_document = session.native_document
        if not native_document:
            return {"ok": False, "native": False, "error": session.native_error or "Native key input is not available."}

        text = str(key.get("text") or "")
        if text:
            result = native_document.type_text(text)
        else:
            result = native_document.post_key_event(
                str(key.get("type") or "down"),
                char_code=int(key.get("char_code") or 0),
                key_code=int(key.get("key_code") or 0),
            )
        session.native_metadata = native_document.metadata()
        session.updated_at = time.time()
        return {**result, "metadata": session.native_metadata, "tiles": self.tiles(session_id)}

    def mouse(self, session_id: str, mouse: dict[str, Any]) -> dict[str, Any]:
        session = self._require(session_id)
        native_document = session.native_document
        if not native_document:
            return {"ok": False, "native": False, "error": session.native_error or "Native mouse input is not available."}

        result = native_document.post_mouse_event(
            str(mouse.get("type") or "down"),
            int(mouse.get("x") or 0),
            int(mouse.get("y") or 0),
            count=int(mouse.get("count") or 1),
            buttons=int(mouse.get("buttons") or 1),
            modifier=int(mouse.get("modifier") or 0),
        )
        session.native_metadata = native_document.metadata()
        session.updated_at = time.time()
        return {**result, "metadata": session.native_metadata, "tiles": self.tiles(session_id)}

    def cursor(self, session_id: str, cursor: dict[str, Any]) -> dict[str, Any]:
        session = self._require(session_id)
        session.cursor = dict(cursor or {})
        session.updated_at = time.time()
        return {"ok": True, "session_id": session_id, "cursor": session.cursor}

    def selection(self, session_id: str, selection: dict[str, Any]) -> dict[str, Any]:
        session = self._require(session_id)
        session.selection = dict(selection or {})
        session.updated_at = time.time()
        return {"ok": True, "session_id": session_id, "selection": session.selection}

    def tiles(self, session_id: str) -> list[dict[str, Any]]:
        session = self._require(session_id)
        if session.extension == "docx" and session.native_document:
            try:
                return session.native_document.render_tiles()
            except Exception as exc:
                session.native_error = str(exc)
        if session.extension == "docx":
            return _docx_text_tiles(session.text)
        if session.extension == "md":
            return _markdown_text_tiles(session.text)
        doc = document_store.get_document(session.file_id)
        preview = document_store.build_preview(doc)
        return [{"index": 0, "kind": preview.get("kind") or "file", "preview": preview}]

    def save(self, session_id: str, text: str | None = None) -> dict[str, Any]:
        session = self._require(session_id)
        if text is not None:
            session.text = str(text)

        doc = document_store.get_document(session.file_id)
        if session.extension == "md":
            updated = document_store.write_markdown(session.file_id, session.text)
            session.updated_at = time.time()
            return {"ok": True, "document": _public_doc(updated), "tiles": self.tiles(session_id), "native": self._native_payload(session)}

        if session.extension == "docx":
            from plugins._office.helpers import artifact_editor

            if session.native_document and text is None:
                updated = document_store.replace_document_bytes(
                    session.file_id,
                    session.native_document.save_to_bytes(".docx", "docx"),
                    actor="libreofficekit:save",
                    invalidate_sessions=False,
                )
            else:
                updated, _payload = artifact_editor.edit_artifact(
                    doc,
                    operation="set_text",
                    content=session.text,
                    invalidate_sessions=False,
                )
            validation = libreoffice.validate_docx(updated["path"])
            if not validation.get("ok"):
                return {"ok": False, "error": validation.get("error") or "DOCX save verification failed."}
            self._reopen_native_document(session, updated["path"])
            session.updated_at = time.time()
            return {
                "ok": True,
                "document": _public_doc(updated),
                "tiles": self.tiles(session_id),
                "validation": validation,
                "native": self._native_payload(session),
            }

        return {"ok": False, "error": f"Canvas editing is not available for .{session.extension}."}

    def command(self, session_id: str, command: str, arguments: Any = None, notify: bool = True) -> dict[str, Any]:
        session = self._require(session_id)
        native_document = session.native_document
        if not native_document:
            return {
                "ok": False,
                "native": False,
                "error": session.native_error or f"Native LibreOfficeKit commands are not available for .{session.extension}.",
            }
        result = native_document.post_uno_command(command, arguments=arguments, notify=notify)
        session.native_metadata = native_document.metadata()
        session.updated_at = time.time()
        return {**result, "metadata": session.native_metadata, "tiles": self.tiles(session_id)}

    def command_values(self, session_id: str, command: str) -> dict[str, Any]:
        session = self._require(session_id)
        native_document = session.native_document
        if not native_document:
            return {
                "ok": False,
                "native": False,
                "error": session.native_error or f"Native LibreOfficeKit command values are not available for .{session.extension}.",
            }
        return native_document.command_values(command)

    def refresh_document(self, file_id: str) -> dict[str, Any]:
        normalized = str(file_id or "").strip()
        if not normalized:
            return {"ok": True, "refreshed": 0, "sessions": []}
        try:
            doc = document_store.get_document(normalized)
        except Exception:
            return {"ok": False, "refreshed": 0, "sessions": []}

        refreshed: list[str] = []
        for session in self._sessions.values():
            if session.file_id != normalized:
                continue
            if session.extension in {"md", "docx"}:
                session.text = document_store.read_text_for_editor(doc)
            if session.extension == "docx":
                self._reopen_native_document(session, doc["path"])
            session.updated_at = time.time()
            refreshed.append(session.session_id)
        return {"ok": True, "refreshed": len(refreshed), "sessions": refreshed}

    def close(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.pop(str(session_id or ""), None)
        if not session:
            return {"ok": True, "closed": 0}
        self._close_native_document(session)
        return {"ok": True, "closed": 1, "session_id": session_id}

    def close_sid(self, sid: str) -> int:
        doomed = [session_id for session_id, session in self._sessions.items() if session.sid == sid]
        for session_id in doomed:
            session = self._sessions.pop(session_id, None)
            if session:
                self._close_native_document(session)
        return len(doomed)

    def _payload(self, session: EditorSession, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "session_id": session.session_id,
            "file_id": session.file_id,
            "title": session.title,
            "extension": session.extension,
            "path": session.path,
            "text": session.text,
            "tiles": self.tiles(session.session_id),
            "document": _public_doc(doc),
            "version": document_store.item_version(doc),
            "libreoffice": libreoffice.collect_status(),
            "native": self._native_payload(session),
        }

    def _require(self, session_id: str) -> EditorSession:
        normalized = str(session_id or "").strip()
        session = self._sessions.get(normalized)
        if not session:
            raise FileNotFoundError(f"Editor session not found: {normalized}")
        return session

    def _native_payload(self, session: EditorSession) -> dict[str, Any]:
        if session.native_document:
            return {"available": True, **session.native_metadata}
        return {"available": False, "error": session.native_error}

    def _reopen_native_document(self, session: EditorSession, path: str) -> None:
        self._close_native_document(session)
        try:
            session.native_document = libreofficekit_native.open_document(path)
            session.native_metadata = session.native_document.metadata()
            session.native_error = ""
        except Exception as exc:
            session.native_document = None
            session.native_metadata = {}
            session.native_error = str(exc)

    def _close_native_document(self, session: EditorSession) -> None:
        native_document = session.native_document
        if native_document:
            try:
                native_document.close()
            except Exception:
                pass
        session.native_document = None


def get_manager() -> LibreOfficeKitSessionManager:
    global _manager
    try:
        return _manager
    except NameError:
        _manager = LibreOfficeKitSessionManager()
        return _manager


def _public_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": doc["file_id"],
        "path": document_store.display_path(doc["path"]),
        "basename": doc["basename"],
        "extension": doc["extension"],
        "size": doc["size"],
        "version": document_store.item_version(doc),
        "last_modified": doc["last_modified"],
        "exists": Path(doc["path"]).exists(),
    }


def _apply_text_patch(text: str, patch: dict[str, Any]) -> str:
    if "content" in patch:
        return str(patch.get("content") or "")
    start = int(patch.get("start") or 0)
    end = int(patch.get("end") if patch.get("end") is not None else start)
    replacement = str(patch.get("text") or "")
    start = max(0, min(len(text), start))
    end = max(start, min(len(text), end))
    return text[:start] + replacement + text[end:]


def _markdown_text_tiles(text: str) -> list[dict[str, Any]]:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    return [{"index": 0, "kind": "markdown", "lines": lines[:36]}]


def _docx_text_tiles(text: str) -> list[dict[str, Any]]:
    paragraphs = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not paragraphs:
        paragraphs = [""]
    pages = []
    for index in range(0, len(paragraphs), 18):
        pages.append({
            "index": len(pages),
            "kind": "docx",
            "lines": paragraphs[index:index + 18],
        })
    return pages
