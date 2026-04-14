from __future__ import annotations

from typing import Any, TypedDict


_FRESHNESS_KEY = "_a0_connector_text_editor_remote_mtimes"


class FileMetadata(TypedDict):
    realpath: str
    mtime: float | None
    total_lines: int


def coerce_file_metadata(file_data: Any) -> FileMetadata | None:
    if not isinstance(file_data, dict):
        return None

    realpath = str(file_data.get("realpath", "")).strip()
    if not realpath:
        return None

    try:
        total_lines = int(file_data.get("total_lines", 0))
    except (TypeError, ValueError):
        return None

    raw_mtime = file_data.get("mtime")
    if raw_mtime is None:
        mtime: float | None = None
    else:
        try:
            mtime = float(raw_mtime)
        except (TypeError, ValueError):
            mtime = None

    return FileMetadata(
        realpath=realpath,
        mtime=mtime,
        total_lines=max(total_lines, 0),
    )


def record_file_state(agent, file_data: Any) -> None:
    file_meta = coerce_file_metadata(file_data)
    if file_meta is None or file_meta["mtime"] is None:
        return

    freshness = agent.data.setdefault(_FRESHNESS_KEY, {})
    freshness[file_meta["realpath"]] = {
        "mtime": file_meta["mtime"],
        "total_lines": file_meta["total_lines"],
    }


def mark_file_state_stale(agent, file_data: Any) -> None:
    file_meta = coerce_file_metadata(file_data)
    if file_meta is None:
        return

    freshness = agent.data.setdefault(_FRESHNESS_KEY, {})
    freshness[file_meta["realpath"]] = {"mtime": 0, "total_lines": 0}


def check_patch_freshness(agent, file_data: Any) -> str | None:
    file_meta = coerce_file_metadata(file_data)
    if file_meta is None:
        return "patch_need_read"

    freshness = agent.data.get(_FRESHNESS_KEY, {})
    realpath = file_meta["realpath"]
    if realpath not in freshness:
        return "patch_need_read"

    stored = freshness[realpath]
    mtime = stored.get("mtime") if isinstance(stored, dict) else stored
    if mtime is None:
        freshness.pop(realpath, None)
        return "patch_need_read"

    current = file_meta["mtime"]
    if current is None:
        return None
    if current != mtime:
        return "patch_stale_read"
    return None


def apply_patch_post_state(agent, file_data: Any, edits: list[Any] | None) -> None:
    file_meta = coerce_file_metadata(file_data)
    if file_meta is None:
        return

    freshness = agent.data.setdefault(_FRESHNESS_KEY, {})
    realpath = file_meta["realpath"]

    if not _all_edits_in_place(edits):
        freshness[realpath] = {"mtime": 0, "total_lines": 0}
        return

    stored = freshness.get(realpath)
    if not isinstance(stored, dict) or "total_lines" not in stored:
        freshness[realpath] = {"mtime": 0, "total_lines": 0}
        return

    if file_meta["total_lines"] != int(stored["total_lines"]):
        freshness[realpath] = {"mtime": 0, "total_lines": 0}
        return

    if file_meta["mtime"] is None:
        freshness[realpath] = {"mtime": 0, "total_lines": 0}
        return

    freshness[realpath] = {
        "mtime": file_meta["mtime"],
        "total_lines": file_meta["total_lines"],
    }


def _all_edits_in_place(edits: list[Any] | None) -> bool:
    if not isinstance(edits, list):
        return False

    for edit in edits:
        if not isinstance(edit, dict):
            return False

        try:
            start = int(edit.get("from", 0) or 0)
        except (TypeError, ValueError):
            return False
        if start < 1:
            return False

        raw_to = edit.get("to")
        if raw_to is None:
            return False

        try:
            end = int(raw_to)
        except (TypeError, ValueError):
            return False
        if end < start:
            return False

        removed = end - start + 1
        added = _count_content_lines(edit.get("content"))
        if removed != added:
            return False

    return True


def _count_content_lines(content: Any) -> int:
    if not content:
        return 0

    text = str(content)
    return text.count("\n") + (1 if not text.endswith("\n") else 0)
