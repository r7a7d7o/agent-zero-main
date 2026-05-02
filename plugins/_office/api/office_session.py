from __future__ import annotations

from helpers.api import ApiHandler, Request
from plugins._office.helpers import document_store, libreoffice, libreoffice_desktop, libreofficekit_sessions


class OfficeSession(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        action = str(input.get("action") or "open").lower().strip()
        context_id = str(input.get("ctxid") or input.get("context_id") or "").strip()

        if action == "status":
            return libreoffice.collect_status()
        if action == "home":
            return {"ok": True, "path": document_store.default_open_path(context_id)}
        if action == "recent":
            return {"ok": True, "documents": _public_docs(document_store.get_recent_documents())}
        if action == "open_documents":
            return {"ok": True, "documents": _public_docs(document_store.get_open_documents(limit=24))}
        if action == "desktop":
            return self._desktop()
        if action == "sync_open_sessions":
            session_ids = input.get("session_ids")
            if not isinstance(session_ids, list):
                session_ids = []
            closed = document_store.sync_open_sessions(session_ids)
            return {"ok": True, "closed": closed, "documents": _public_docs(document_store.get_open_documents(limit=24))}
        if action == "close":
            closed = document_store.close_session(
                session_id=str(input.get("session_id") or ""),
                file_id=str(input.get("file_id") or ""),
            )
            return {"ok": True, "closed": closed, "documents": _public_docs(document_store.get_open_documents(limit=24))}
        if action == "create":
            try:
                doc = document_store.create_document(
                    kind=str(input.get("kind") or "document"),
                    title=str(input.get("title") or "Untitled"),
                    fmt=str(input.get("format") or "md"),
                    content=str(input.get("content") or ""),
                    path=str(input.get("path") or ""),
                    context_id=context_id,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if doc["extension"] == "docx":
                validation = libreoffice.validate_docx(doc["path"])
                if not validation.get("ok"):
                    return {"ok": False, "error": validation.get("error") or "DOCX validation failed."}
            return await self._open_document(doc, input, request)
        if action == "open":
            file_id = str(input.get("file_id") or "").strip()
            try:
                doc = (
                    document_store.get_document(file_id)
                    if file_id
                    else document_store.register_document(str(input.get("path") or ""), context_id=context_id)
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return await self._open_document(doc, input, request)
        if action == "save":
            return self._save(input)
        if action == "desktop_save":
            return self._desktop_save(input)
        if action == "desktop_sync":
            return self._desktop_sync(input)
        if action == "desktop_close":
            return self._desktop_close(input)
        if action == "key":
            return libreofficekit_sessions.get_manager().key(
                str(input.get("session_id") or ""),
                input.get("key") if isinstance(input.get("key"), dict) else {},
            )
        if action == "mouse":
            return libreofficekit_sessions.get_manager().mouse(
                str(input.get("session_id") or ""),
                input.get("mouse") if isinstance(input.get("mouse"), dict) else {},
            )
        if action == "command":
            return libreofficekit_sessions.get_manager().command(
                str(input.get("session_id") or ""),
                str(input.get("command") or ""),
                arguments=input.get("arguments"),
                notify=bool(input.get("notify", True)),
            )
        if action == "command_values":
            return libreofficekit_sessions.get_manager().command_values(
                str(input.get("session_id") or ""),
                str(input.get("command") or ""),
            )
        if action == "export":
            return self._export(input)
        return {"ok": False, "error": f"Unsupported office session action: {action}"}

    async def _open_document(self, doc: dict, input: dict, request: Request) -> dict:
        mode = "edit" if str(input.get("mode") or "edit").lower() == "edit" else "view"
        store_session = document_store.create_session(
            doc["file_id"],
            user_id=str(input.get("user_id") or "agent-zero-user"),
            permission="write" if mode == "edit" else "read",
            origin=self._origin(request),
        )
        if str(doc.get("extension") or "").lower() in libreoffice_desktop.OFFICIAL_EXTENSIONS:
            desktop = libreoffice_desktop.get_manager().open(doc)
            if not desktop.get("available"):
                document_store.close_session(session_id=store_session["session_id"])
                return {
                    "ok": False,
                    "error": desktop.get("error") or desktop.get("reason") or "Official LibreOffice desktop session is unavailable.",
                    "desktop": desktop,
                    "libreoffice": libreoffice.collect_status(),
                }
            return {
                "ok": True,
                "session_id": desktop["session_id"],
                "desktop_session_id": desktop["session_id"],
                "file_id": doc["file_id"],
                "title": doc["basename"],
                "extension": doc["extension"],
                "path": doc["path"],
                "text": "",
                "tiles": [],
                "document": _public_doc(doc),
                "version": document_store.item_version(doc),
                "libreoffice": libreoffice.collect_status(),
                "native": {"available": False, "mode": "desktop"},
                "desktop": desktop,
                "store_session_id": store_session["session_id"],
                "preview": document_store.build_preview(doc),
                "mode": mode,
            }
        editor = libreofficekit_sessions.get_manager().open(doc, sid="")
        return {
            **editor,
            "store_session_id": store_session["session_id"],
            "session_id": editor["session_id"],
            "preview": document_store.build_preview(doc),
            "mode": mode,
        }

    def _save(self, input: dict) -> dict:
        session_id = str(input.get("session_id") or "").strip()
        if not session_id:
            return {"ok": False, "error": "session_id is required."}
        return libreofficekit_sessions.get_manager().save(session_id, text=input.get("text"))

    def _desktop(self) -> dict:
        desktop = libreoffice_desktop.get_manager().ensure_system_desktop()
        if not desktop.get("available"):
            return {
                "ok": False,
                "error": desktop.get("error") or "Official LibreOffice desktop session is unavailable.",
                "desktop": desktop,
                "libreoffice": libreoffice.collect_status(),
            }
        document = {
            "file_id": libreoffice_desktop.SYSTEM_FILE_ID,
            "path": desktop["path"],
            "basename": desktop["title"],
            "title": desktop["title"],
            "extension": "desktop",
            "size": 0,
            "version": 0,
            "preview": {},
        }
        return {
            "ok": True,
            "session_id": desktop["session_id"],
            "desktop_session_id": desktop["session_id"],
            "file_id": libreoffice_desktop.SYSTEM_FILE_ID,
            "title": desktop["title"],
            "extension": "desktop",
            "path": desktop["path"],
            "text": "",
            "tiles": [],
            "document": document,
            "version": 0,
            "libreoffice": libreoffice.collect_status(),
            "native": {"available": False, "mode": "desktop"},
            "desktop": desktop,
            "store_session_id": "",
            "preview": {},
            "mode": "desktop",
        }

    def _desktop_save(self, input: dict) -> dict:
        session_id = str(input.get("desktop_session_id") or input.get("session_id") or "").strip()
        if not session_id:
            return {"ok": False, "error": "desktop_session_id is required."}
        return libreoffice_desktop.get_manager().save(
            session_id,
            file_id=str(input.get("file_id") or ""),
        )

    def _desktop_sync(self, input: dict) -> dict:
        return libreoffice_desktop.get_manager().sync(
            session_id=str(input.get("desktop_session_id") or input.get("session_id") or ""),
            file_id=str(input.get("file_id") or ""),
        )

    def _desktop_close(self, input: dict) -> dict:
        session_id = str(input.get("desktop_session_id") or input.get("session_id") or "").strip()
        if not session_id:
            return {"ok": False, "error": "desktop_session_id is required."}
        return libreoffice_desktop.get_manager().close(
            session_id,
            save_first=bool(input.get("save_first", True)),
        )

    def _export(self, input: dict) -> dict:
        file_id = str(input.get("file_id") or "").strip()
        path = str(input.get("path") or "").strip()
        target_format = str(input.get("target_format") or input.get("format") or "pdf").lower().lstrip(".")
        doc = document_store.get_document(file_id) if file_id else document_store.register_document(path)
        result = libreoffice.convert_document(doc["path"], target_format)
        if not result.get("ok"):
            return result
        return {"ok": True, "path": document_store.display_path(result["path"]), "source": _public_doc(doc)}

    def _origin(self, request: Request) -> str:
        origin = request.headers.get("Origin") or request.host_url.rstrip("/")
        return origin.rstrip("/")


def _public_docs(docs: list[dict]) -> list[dict]:
    return [_public_doc(doc) for doc in docs]


def _public_doc(doc: dict) -> dict:
    result = {
        "file_id": doc["file_id"],
        "path": document_store.display_path(doc["path"]),
        "basename": doc["basename"],
        "title": doc["basename"],
        "extension": doc["extension"],
        "size": doc["size"],
        "version": document_store.item_version(doc),
        "last_modified": doc["last_modified"],
        "preview": doc.get("preview") or document_store.build_preview(doc),
    }
    for key in ("open_sessions", "last_opened_at", "session_expires_at"):
        if key in doc:
            result[key] = doc[key]
    return result
