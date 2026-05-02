from __future__ import annotations

from typing import Any

from plugins._office.helpers import document_store


def build_context(max_items: int = 6) -> str:
    documents = document_store.get_open_documents(limit=max_items)
    if not documents:
        return ""

    lines = [
        "These document artifacts have active canvas sessions. Content is omitted; load skill `office-artifacts` for edit workflow, then use document_artifact:read before content-sensitive edits.",
    ]
    for doc in documents:
        lines.append(format_document_line(doc))
    lines.append(
        "Use document_artifact:edit with file_id or path for saved edits; tool results refresh the document canvas."
    )
    return "\n".join(lines)


def format_document_line(doc: dict[str, Any]) -> str:
    return (
        f"- {doc.get('basename', 'Untitled')} "
        f"(.{doc.get('extension', '')}, file_id={doc.get('file_id', '')}, "
        f"path={document_store.display_path(doc.get('path', ''))}, version={document_store.item_version(doc)}, "
        f"size={doc.get('size', 0)} bytes, last_modified={doc.get('last_modified', '')}, "
        f"open_sessions={doc.get('open_sessions', 1)})"
    )
