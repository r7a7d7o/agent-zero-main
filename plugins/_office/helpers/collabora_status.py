from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from helpers import files


PLUGIN_NAME = "_office"
RUNTIME_DIR = Path(files.get_abs_path("tmp", PLUGIN_NAME, "collabora"))
STATE_DIR = Path(files.get_abs_path("usr", "plugins", PLUGIN_NAME, "collabora"))
STATUS_FILE = RUNTIME_DIR / "status.json"
BOOTSTRAP_LOG = RUNTIME_DIR / "bootstrap.log"
WRAPPER_LOG = RUNTIME_DIR / "coolwsd-wrapper.log"
SUPERVISOR_PROGRAM = "a0_office_collabora"


def ensure_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "backups").mkdir(parents=True, exist_ok=True)


def now_ts() -> float:
    return time.time()


def read_status() -> dict[str, Any]:
    ensure_dirs()
    if not STATUS_FILE.exists():
        return default_status("idle")
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {**default_status("idle"), **data}
    except Exception:
        pass
    return default_status("idle")


def write_status(state: str, **extra: Any) -> dict[str, Any]:
    ensure_dirs()
    payload = {
        **read_status(),
        "state": state,
        "updated_at": now_ts(),
        **extra,
    }
    STATUS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def default_status(state: str = "idle") -> dict[str, Any]:
    return {
        "plugin": PLUGIN_NAME,
        "state": state,
        "healthy": False,
        "installed": False,
        "installing": False,
        "degraded": False,
        "message": "",
        "updated_at": 0,
        "runtime_dir": str(RUNTIME_DIR),
        "state_dir": str(STATE_DIR),
        "status_file": str(STATUS_FILE),
        "bootstrap_log": str(BOOTSTRAP_LOG),
        "wrapper_log": str(WRAPPER_LOG),
    }


def append_log(message: str) -> None:
    ensure_dirs()
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n"
    with BOOTSTRAP_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line)


def tail_file(path: Path, max_bytes: int = 16000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except Exception as exc:
        return f"Could not read log: {exc}"


def run_command(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def package_installed(name: str) -> bool:
    if not command_exists("dpkg-query"):
        return False
    result = run_command(["dpkg-query", "-W", "-f=${Status}", name], timeout=8)
    return result.returncode == 0 and "install ok installed" in result.stdout


def packages_installed() -> bool:
    return (
        command_exists("coolwsd")
        and command_exists("coolforkit-caps")
        and package_installed("coolwsd")
        and package_installed("coolwsd-deprecated")
        and package_installed("code-brand")
    )


def supervisor_status() -> str:
    if not command_exists("supervisorctl"):
        return "supervisorctl unavailable"
    result = run_command(["supervisorctl", "status", SUPERVISOR_PROGRAM], timeout=8)
    return (result.stdout or "").strip() or f"exit {result.returncode}"


def process_status() -> str:
    if not command_exists("pgrep"):
        return ""
    result = run_command(["pgrep", "-a", "coolwsd"], timeout=8)
    return (result.stdout or "").strip()


def discovery_ok() -> bool:
    for url in (
        "http://127.0.0.1:9980/office/hosting/discovery",
        "http://127.0.0.1:9980/hosting/discovery",
    ):
        try:
            request = Request(url, headers={"User-Agent": "Agent-Zero-Office/1.0"})
            with urlopen(request, timeout=5) as response:
                body = response.read(256)
                if response.status == 200 and b"wopi-discovery" in body.lower():
                    return True
        except Exception:
            continue
    return False


def collect_status() -> dict[str, Any]:
    ensure_dirs()
    installed = packages_installed()
    supervisor = supervisor_status()
    process = process_status()
    http_ok = discovery_ok()
    healthy = installed and http_ok
    saved = read_status()
    installing = saved.get("state") == "installing"
    state = "healthy" if healthy else ("installing" if installing else ("degraded" if installed else saved.get("state") or "idle"))
    return {
        **saved,
        "state": state,
        "healthy": healthy,
        "installed": installed,
        "installing": installing and not healthy,
        "degraded": installed and not healthy,
        "coolwsd_path": shutil.which("coolwsd") or "",
        "supervisor": supervisor,
        "process": process,
        "discovery_ok": http_ok,
        "updated_at": now_ts(),
    }
