from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from helpers import files


MAX_PATCH_LINES = 2500
MAX_PATCH_BYTES = 240_000
MAX_UNTRACKED_BYTES = 180_000
GIT_TIMEOUT_SECONDS = 8

GROUP_ORDER = ("staged", "unstaged", "untracked")
STATUS_LABELS = {
    "A": "added",
    "C": "copied",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "T": "type_changed",
    "U": "unmerged",
    "?": "untracked",
}


class GitDiffError(RuntimeError):
    pass


def collect_workspace_diff(
    workspace_path: str,
    *,
    context_id: str = "",
    display_path: str | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_path).expanduser().resolve()
    display = display_path or str(workspace)

    if not workspace.exists() or not workspace.is_dir():
        return {
            "ok": False,
            "context_id": context_id,
            "workspace_path": display,
            "is_git_repo": False,
            "error": "Workspace path does not exist or is not a directory.",
            "branch": "",
            "totals": {"files": 0, "additions": 0, "deletions": 0},
            "groups": _empty_groups(),
        }

    if not _is_git_repo(workspace):
        return {
            "ok": True,
            "context_id": context_id,
            "workspace_path": display,
            "is_git_repo": False,
            "branch": "",
            "totals": {"files": 0, "additions": 0, "deletions": 0},
            "groups": _empty_groups(),
        }

    groups = [
        {"kind": "staged", "files": _collect_diff_group(workspace, "staged")},
        {"kind": "unstaged", "files": _collect_diff_group(workspace, "unstaged")},
        {"kind": "untracked", "files": _collect_untracked_group(workspace)},
    ]

    seen_paths: set[str] = set()
    additions = 0
    deletions = 0
    for group in groups:
        for item in group["files"]:
            seen_paths.add(str(item.get("path") or item.get("old_path") or ""))
            additions += int(item.get("additions") or 0)
            deletions += int(item.get("deletions") or 0)

    return {
        "ok": True,
        "context_id": context_id,
        "workspace_path": display,
        "is_git_repo": True,
        "branch": _branch_name(workspace),
        "totals": {
            "files": len([path for path in seen_paths if path]),
            "additions": additions,
            "deletions": deletions,
        },
        "groups": groups,
    }


def _empty_groups() -> list[dict[str, Any]]:
    return [{"kind": kind, "files": []} for kind in GROUP_ORDER]


def _git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if check and completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Git command failed.").strip()
        raise GitDiffError(message)
    return completed


def _is_git_repo(workspace: Path) -> bool:
    return _git(workspace, "rev-parse", "--is-inside-work-tree", check=False).returncode == 0


def _branch_name(workspace: Path) -> str:
    branch = _git(workspace, "branch", "--show-current", check=False).stdout.strip()
    if branch:
        return branch
    return _git(workspace, "rev-parse", "--short", "HEAD", check=False).stdout.strip()


def _collect_diff_group(workspace: Path, kind: str) -> list[dict[str, Any]]:
    cached_args = ["--cached"] if kind == "staged" else []
    status_output = _git(
        workspace,
        "diff",
        *cached_args,
        "--name-status",
        "-z",
        "--find-renames",
        "--relative",
        "--",
        ".",
    ).stdout
    entries = _parse_name_status(status_output)
    result: list[dict[str, Any]] = []

    for entry in entries:
        path = entry["path"]
        old_path = entry.get("old_path", "")
        if _is_a0_metadata_path(path) and (not old_path or _is_a0_metadata_path(old_path)):
            continue

        stat_paths = [candidate for candidate in (old_path, path) if candidate]
        additions, deletions, binary = _diff_numstat(workspace, kind, stat_paths)
        if _is_zero_line_gitkeep_change(path, old_path, additions, deletions):
            continue

        too_large = additions + deletions > MAX_PATCH_LINES
        patch = ""
        if not binary and not too_large:
            patch = _diff_patch(workspace, kind, stat_paths)
            if len(patch.encode("utf-8", errors="replace")) > MAX_PATCH_BYTES:
                patch = ""
                too_large = True

        result.append(
            {
                "path": path,
                "old_path": old_path,
                "status": STATUS_LABELS.get(entry["status"], entry["status"].lower()),
                "additions": additions,
                "deletions": deletions,
                "binary": binary,
                "too_large": too_large,
                "patch": patch,
            }
        )

    return result


def _collect_untracked_group(workspace: Path) -> list[dict[str, Any]]:
    output = _git(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        ".",
    ).stdout
    result: list[dict[str, Any]] = []
    for path in [part for part in output.split("\0") if part]:
        path = path.replace("\\", "/")
        if _is_a0_metadata_path(path):
            continue
        item = _untracked_file_diff(workspace, path)
        if _is_zero_line_gitkeep_change(item["path"], item.get("old_path", ""), item["additions"], item["deletions"]):
            continue
        result.append(item)
    result.sort(key=lambda item: item["path"])
    return result


def _parse_name_status(output: str) -> list[dict[str, str]]:
    parts = [part for part in output.split("\0") if part]
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(parts):
        raw_status = parts[index]
        index += 1
        status = raw_status[:1]
        if status in {"R", "C"} and index + 1 < len(parts):
            old_path = parts[index].replace("\\", "/")
            new_path = parts[index + 1].replace("\\", "/")
            index += 2
            entries.append({"status": status, "old_path": old_path, "path": new_path})
            continue
        if index < len(parts):
            path = parts[index].replace("\\", "/")
            index += 1
            entries.append({"status": status, "path": path, "old_path": ""})
    entries.sort(key=lambda item: item.get("path") or item.get("old_path") or "")
    return entries


def _diff_numstat(workspace: Path, kind: str, paths: list[str]) -> tuple[int, int, bool]:
    args = ["diff"]
    if kind == "staged":
        args.append("--cached")
    output = _git(
        workspace,
        *args,
        "--numstat",
        "--find-renames",
        "--relative",
        "--",
        *paths,
    ).stdout
    first = next((line for line in output.splitlines() if line.strip()), "")
    if not first:
        return 0, 0, False
    parts = first.split("\t")
    if len(parts) < 2:
        return 0, 0, False
    if parts[0] == "-" or parts[1] == "-":
        return 0, 0, True
    return _safe_int(parts[0]), _safe_int(parts[1]), False


def _diff_patch(workspace: Path, kind: str, paths: list[str]) -> str:
    args = ["diff"]
    if kind == "staged":
        args.append("--cached")
    return _git(
        workspace,
        *args,
        "--patch",
        "--find-renames",
        "--relative",
        "--",
        *paths,
    ).stdout


def _untracked_file_diff(workspace: Path, path: str) -> dict[str, Any]:
    file_path = (workspace / path).resolve()
    additions = 0
    binary = False
    too_large = False
    patch = ""

    try:
        with open(file_path, "rb") as handle:
            data = handle.read(MAX_UNTRACKED_BYTES + 1)
        too_large = len(data) > MAX_UNTRACKED_BYTES
        sample = data[: min(len(data), 10 * 1024)]
        binary = files.is_probably_binary_bytes(sample)
        if not binary and not too_large:
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            additions = len(lines)
            if len(lines) > MAX_PATCH_LINES:
                too_large = True
            else:
                patch = _synthetic_untracked_patch(path, lines, text.endswith("\n"))
        elif not binary:
            additions = _count_newlines(data[:MAX_UNTRACKED_BYTES])
    except OSError:
        too_large = True

    return {
        "path": path,
        "old_path": "",
        "status": "untracked",
        "additions": additions,
        "deletions": 0,
        "binary": binary,
        "too_large": too_large,
        "patch": patch,
    }


def _synthetic_untracked_patch(path: str, lines: list[str], has_trailing_newline: bool) -> str:
    escaped = path.replace("\t", "\\t")
    header = [
        f"diff --git a/{escaped} b/{escaped}",
        "new file mode 100644",
        "index 0000000..0000000",
        "--- /dev/null",
        f"+++ b/{escaped}",
    ]
    if not lines:
        return "\n".join(header) + "\n"
    body = [f"@@ -0,0 +1,{len(lines)} @@"]
    body.extend(f"+{line}" for line in lines)
    if not has_trailing_newline:
        body.append("\\ No newline at end of file")
    return "\n".join(header + body) + "\n"


def _count_newlines(data: bytes) -> int:
    if not data:
        return 0
    count = data.count(b"\n")
    return count if data.endswith(b"\n") else count + 1


def _safe_int(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _is_a0_metadata_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    return normalized == ".a0proj" or normalized.startswith(".a0proj/")


def _is_zero_line_gitkeep_change(path: str, old_path: str, additions: int, deletions: int) -> bool:
    if additions != 0 or deletions != 0:
        return False
    candidates = [path, old_path]
    return any(
        candidate.replace("\\", "/").rstrip("/").split("/")[-1] == ".gitkeep"
        for candidate in candidates
        if candidate
    )
