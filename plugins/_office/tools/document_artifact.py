from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helpers.tool import Response, Tool
from plugins._office.helpers import collabora_status, wopi_store


class DocumentArtifact(Tool):
    async def execute(
        self,
        action: str = "",
        kind: str = "document",
        title: str = "Untitled",
        format: str = "docx",
        content: str = "",
        path: str = "",
        file_id: str = "",
        version_id: int | str | None = None,
        **kwargs: Any,
    ) -> Response:
        action = str(action or self.method or "status").strip().lower().replace("-", "_")
        try:
            if action == "create":
                doc = wopi_store.create_document(kind=kind, title=title, fmt=format, content=content, path=path)
                return self._document_response("Created document artifact.", doc)
            if action == "open":
                doc = self._document_from_input(file_id=file_id, path=path)
                return self._document_response("Opened document artifact.", doc)
            if action == "inspect":
                doc = self._document_from_input(file_id=file_id, path=path)
                return self._json_response({"ok": True, "document": self._public_doc(doc)}, doc=doc)
            if action == "version_history":
                doc = self._document_from_input(file_id=file_id, path=path)
                versions = wopi_store.version_history(doc["file_id"])
                return self._json_response({"ok": True, "versions": versions}, doc=doc)
            if action == "restore_version":
                if version_id is None or str(version_id).strip() == "":
                    return Response(message="version_id is required for restore_version.", break_loop=False)
                doc = self._document_from_input(file_id=file_id, path=path)
                restored = wopi_store.restore_version(doc["file_id"], int(version_id))
                return self._document_response("Restored document artifact version.", restored)
            if action == "export":
                doc = self._document_from_input(file_id=file_id, path=path)
                target_format = str(kwargs.get("target_format") or kwargs.get("export_format") or "").lower().lstrip(".")
                if target_format and target_format != doc["extension"]:
                    return Response(
                        message=f"Export to .{target_format} is not available yet. The source file remains unchanged at {doc['path']}.",
                        break_loop=False,
                        additional=self._additional(doc),
                    )
                return self._document_response("Document artifact export path is ready.", doc)
            if action == "status":
                return self._json_response({"ok": True, "status": collabora_status.collect_status()})
            return Response(message=f"Unknown document_artifact action: {action}", break_loop=False)
        except Exception as exc:
            return Response(message=f"document_artifact {action} failed: {exc}", break_loop=False)

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=f"icon://description {self.agent.agent_name}: Using document artifact",
            content="",
            kvps={**self.args, "_tool_name": self.name},
            _tool_name=self.name,
        )

    def _document_from_input(self, file_id: str = "", path: str = "") -> dict[str, Any]:
        if file_id:
            return wopi_store.get_document(file_id)
        if path:
            return wopi_store.register_document(path)
        raise ValueError("file_id or path is required")

    def _document_response(self, message: str, doc: dict[str, Any]) -> Response:
        payload = {"ok": True, "message": message, "document": self._public_doc(doc)}
        return Response(
            message=json.dumps(payload, indent=2, ensure_ascii=False),
            break_loop=False,
            additional=self._additional(doc),
        )

    def _json_response(self, payload: dict[str, Any], doc: dict[str, Any] | None = None) -> Response:
        return Response(
            message=json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            break_loop=False,
            additional=self._additional(doc) if doc else {"_tool_name": self.name, "canvas_surface": "office"},
        )

    def _additional(self, doc: dict[str, Any] | None) -> dict[str, Any]:
        if not doc:
            return {"_tool_name": self.name, "canvas_surface": "office"}
        return {
            "_tool_name": self.name,
            "canvas_surface": "office",
            "file_id": doc["file_id"],
            "title": doc["basename"],
            "format": doc["extension"],
            "path": doc["path"],
            "version": wopi_store.item_version(doc),
        }

    def _public_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "file_id": doc["file_id"],
            "path": doc["path"],
            "basename": doc["basename"],
            "extension": doc["extension"],
            "size": doc["size"],
            "version": wopi_store.item_version(doc),
            "last_modified": doc["last_modified"],
            "exists": Path(doc["path"]).exists(),
        }
