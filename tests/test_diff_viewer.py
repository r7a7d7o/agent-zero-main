from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._diff_viewer.helpers import diff as diff_helper
from plugins._diff_viewer.helpers.diff import collect_workspace_diff


def run_git(repo_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def init_repo(repo_dir: Path) -> None:
    run_git(repo_dir, "init")
    run_git(repo_dir, "config", "user.name", "Test User")
    run_git(repo_dir, "config", "user.email", "test@example.com")
    (repo_dir / "tracked.txt").write_text("one\n", encoding="utf-8")
    run_git(repo_dir, "add", "tracked.txt")
    run_git(repo_dir, "commit", "-m", "initial")


def files_for_group(payload: dict, kind: str) -> list[dict]:
    return next(group["files"] for group in payload["groups"] if group["kind"] == kind)


def test_collect_workspace_diff_returns_non_git_state(tmp_path: Path) -> None:
    payload = collect_workspace_diff(str(tmp_path), context_id="ctx")

    assert payload["ok"] is True
    assert payload["context_id"] == "ctx"
    assert payload["is_git_repo"] is False
    assert payload["totals"] == {"files": 0, "additions": 0, "deletions": 0}


def test_collect_workspace_diff_groups_staged_unstaged_and_untracked(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("one\nstaged\n", encoding="utf-8")
    run_git(tmp_path, "add", "tracked.txt")
    (tmp_path / "tracked.txt").write_text("one\nstaged\nunstaged\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("hello\n", encoding="utf-8")

    payload = collect_workspace_diff(str(tmp_path))

    staged = files_for_group(payload, "staged")
    unstaged = files_for_group(payload, "unstaged")
    untracked = files_for_group(payload, "untracked")
    assert staged[0]["path"] == "tracked.txt"
    assert staged[0]["status"] == "modified"
    assert "+staged" in staged[0]["patch"]
    assert unstaged[0]["path"] == "tracked.txt"
    assert "+unstaged" in unstaged[0]["patch"]
    assert untracked[0]["path"] == "new.txt"
    assert untracked[0]["status"] == "untracked"
    assert "+hello" in untracked[0]["patch"]
    assert payload["totals"]["files"] == 2
    assert payload["totals"]["additions"] == 3


def test_collect_workspace_diff_ignores_zero_line_gitkeep(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "real.txt").write_text("real\n", encoding="utf-8")
    run_git(tmp_path, "add", ".gitkeep")

    payload = collect_workspace_diff(str(tmp_path))
    paths = [
        item["path"]
        for group in payload["groups"]
        for item in group["files"]
    ]

    assert ".gitkeep" not in paths
    assert "nested/.gitkeep" not in paths
    assert paths == ["real.txt"]
    assert payload["totals"] == {"files": 1, "additions": 1, "deletions": 0}


def test_collect_workspace_diff_deleted_renamed_binary_large_and_a0_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_repo(tmp_path)
    (tmp_path / "rename_me.txt").write_text("move\n", encoding="utf-8")
    (tmp_path / "delete_me.txt").write_text("delete\n", encoding="utf-8")
    run_git(tmp_path, "add", "rename_me.txt", "delete_me.txt")
    run_git(tmp_path, "commit", "-m", "fixtures")

    run_git(tmp_path, "mv", "rename_me.txt", "renamed.txt")
    (tmp_path / "delete_me.txt").unlink()
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01data")
    (tmp_path / ".a0proj").mkdir()
    (tmp_path / ".a0proj" / "project.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(diff_helper, "MAX_UNTRACKED_BYTES", 10)
    (tmp_path / "large.txt").write_text("0123456789\n" * 5, encoding="utf-8")

    payload = collect_workspace_diff(str(tmp_path))
    staged = files_for_group(payload, "staged")
    unstaged = files_for_group(payload, "unstaged")
    untracked = files_for_group(payload, "untracked")

    git_changes = staged + unstaged
    statuses = {(item["path"], item["status"]) for item in git_changes}
    assert ("delete_me.txt", "deleted") in statuses
    assert ("renamed.txt", "renamed") in statuses
    renamed = next(item for item in git_changes if item["path"] == "renamed.txt")
    assert renamed["old_path"] == "rename_me.txt"
    assert renamed["additions"] == 0
    assert renamed["deletions"] == 0
    binary = next(item for item in untracked if item["path"] == "binary.bin")
    assert binary["binary"] is True
    large = next(item for item in untracked if item["path"] == "large.txt")
    assert large["too_large"] is True
    assert all(not item["path"].startswith(".a0proj") for item in untracked)


def test_collect_workspace_diff_limits_nested_workspace_to_pathspec(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "inside.txt").write_text("inside\n", encoding="utf-8")

    payload = collect_workspace_diff(str(tmp_path / "nested"))
    untracked = files_for_group(payload, "untracked")

    assert [item["path"] for item in untracked] == ["inside.txt"]


def test_diff_api_resolves_project_context_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("whisper")
    pytest.importorskip("langchain_core")
    from plugins._diff_viewer.api import diff as diff_api

    init_repo(tmp_path)
    (tmp_path / "changed.txt").write_text("changed\n", encoding="utf-8")
    handler = diff_api.Diff(Flask("diff-test"), threading.RLock())
    monkeypatch.setattr(handler, "use_context", lambda context_id: SimpleNamespace(id=context_id))
    monkeypatch.setattr(diff_api.projects, "get_context_project_name", lambda _context: "demo")
    monkeypatch.setattr(diff_api.projects, "get_project_folder", lambda _name: str(tmp_path))
    monkeypatch.setattr(diff_api.files, "normalize_a0_path", lambda path: path)
    monkeypatch.setattr(diff_api.files, "fix_dev_path", lambda path: path)

    payload = asyncio.run(handler.process({"context_id": "ctx-project"}, None))

    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["context_id"] == "ctx-project"
    assert payload["workspace_path"] == str(tmp_path)
    assert files_for_group(payload, "untracked")[0]["path"] == "changed.txt"


def test_diff_api_falls_back_to_default_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("whisper")
    pytest.importorskip("langchain_core")
    from plugins._diff_viewer.api import diff as diff_api

    init_repo(tmp_path)
    (tmp_path / "workdir.txt").write_text("workdir\n", encoding="utf-8")
    handler = diff_api.Diff(Flask("diff-test-default"), threading.RLock())
    monkeypatch.setattr(diff_api.settings, "get_settings", lambda: {"workdir_path": str(tmp_path)})
    monkeypatch.setattr(diff_api.files, "fix_dev_path", lambda path: path)

    payload = asyncio.run(handler.process({}, None))

    assert isinstance(payload, dict)
    assert payload["workspace_path"] == str(tmp_path)
    assert files_for_group(payload, "untracked")[0]["path"] == "workdir.txt"
