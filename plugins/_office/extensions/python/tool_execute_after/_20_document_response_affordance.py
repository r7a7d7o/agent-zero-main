from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helpers import files
from helpers.extension import Extension
from helpers.print_style import PrintStyle
from helpers.tool import Response
from plugins._office.helpers import document_affordance, wopi_store


HANDOFF_CREATED_FLAG = "_office_document_handoff_created"


class DocumentResponseAffordance(Extension):
    async def execute(
        self,
        tool_name: str = "",
        response: Response | None = None,
        **kwargs: Any,
    ):
        if not self.agent or response is None:
            return

        if tool_name == "document_artifact":
            if (response.additional or {}).get("file_id"):
                self.agent.loop_data.params_persistent[HANDOFF_CREATED_FLAG] = True
            return

        if tool_name != "response":
            return

        tool = self.agent.loop_data.current_tool
        if not tool:
            return
        if self.agent.loop_data.params_persistent.get(HANDOFF_CREATED_FLAG):
            return

        text = str(tool.args.get("text") or tool.args.get("message") or response.message or "").strip()
        user_message = self.agent.last_user_message.content if self.agent.last_user_message else ""
        decision = document_affordance.decide_response_artifact(user_message, text)
        if decision is None:
            return

        try:
            doc = wopi_store.create_document(
                kind=decision.kind,
                title=decision.title,
                fmt=decision.fmt,
                content=decision.content,
            )
        except Exception as exc:
            PrintStyle().error(f"Office document affordance failed: {exc}")
            return

        payload = {
            "ok": True,
            "message": "Created document artifact from response.",
            "document": public_doc(doc),
        }
        additional = document_additional(doc)
        content = json.dumps(payload, indent=2, ensure_ascii=False)

        self.agent.hist_add_tool_result("document_artifact", content, **additional)
        self.agent.loop_data.params_persistent[HANDOFF_CREATED_FLAG] = True

        display_path = display_workspace_path(doc["path"])
        note = document_affordance.format_created_response(doc["basename"], display_path)
        response.message = note
        tool.args["text"] = note
        tool.args["message"] = note

        log_item = self.agent.loop_data.params_temporary.get("log_item_response")
        if log_item:
            log_item.update(
                content=note,
                kvps={
                    "action": "create",
                    "kind": decision.kind,
                    "title": decision.title,
                    "format": decision.fmt,
                    "_tool_name": "document_artifact",
                    **additional,
                },
            )


def public_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": doc["file_id"],
        "path": display_workspace_path(doc["path"]),
        "basename": doc["basename"],
        "extension": doc["extension"],
        "size": doc["size"],
        "version": wopi_store.item_version(doc),
        "last_modified": doc["last_modified"],
        "exists": Path(doc["path"]).exists(),
    }


def document_additional(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "_tool_name": "document_artifact",
        "canvas_surface": "office",
        "file_id": doc["file_id"],
        "title": doc["basename"],
        "format": doc["extension"],
        "path": display_workspace_path(doc["path"]),
        "version": wopi_store.item_version(doc),
    }


def display_workspace_path(path: str) -> str:
    base = Path(files.get_base_dir()).resolve(strict=False)
    resolved = Path(path).resolve(strict=False)
    if str(base).startswith("/a0"):
        return str(resolved)
    try:
        return "/a0/" + str(resolved.relative_to(base)).lstrip("/")
    except ValueError:
        return str(path)
