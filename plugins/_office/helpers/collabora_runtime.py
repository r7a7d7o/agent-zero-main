from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from plugins._office.helpers import collabora_status as status


LOCK_FILE = status.RUNTIME_DIR / "bootstrap.lock"
WRAPPER_FILE = status.RUNTIME_DIR / "run_coolwsd.sh"
SUPERVISOR_CONF = Path("/etc/supervisor/conf.d/a0_office_collabora.conf")
SUPERVISOR_INCLUDE_PATTERN = "/etc/supervisor/conf.d/a0_office_*.conf"
SOURCES_FILE = Path("/etc/apt/sources.list.d/collaboraonline.sources")
KEYRING_FILE = Path("/etc/apt/keyrings/collaboraonline-release-keyring.gpg")

_worker_lock = threading.Lock()
_worker: threading.Thread | None = None


def start_bootstrap_worker(force: bool = False) -> bool:
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return False
        _worker = threading.Thread(target=bootstrap, kwargs={"force": force}, name="a0-office-collabora-bootstrap", daemon=True)
        _worker.start()
        return True


def bootstrap(force: bool = False) -> None:
    status.ensure_dirs()
    with LOCK_FILE.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            status.append_log("bootstrap already running")
            return

        try:
            _bootstrap_locked(force=force)
        except Exception as exc:
            status.append_log(f"bootstrap failed: {exc}")
            status.write_status("failed", healthy=False, installing=False, message=str(exc))


def _bootstrap_locked(force: bool = False) -> None:
    status.write_status("installing", healthy=False, installing=True, message="Preparing Collabora Online")
    status.append_log("bootstrap start")

    _write_wrapper()
    _write_supervisor_conf()
    _reread_supervisor()

    if status.packages_installed() and not force:
        status.append_log("coolwsd and code-brand already installed")
        _restart_supervisor()
        _finish_status()
        return

    if not _can_install():
        status.write_status("degraded", healthy=False, installing=False, message="Container does not support automatic apt installation")
        _restart_supervisor()
        return

    _ensure_code_repo()
    _wait_for_apt_locks()
    _run(["apt-get", "update"], timeout=600)
    _run([
        "apt-get",
        "install",
        "-y",
        "--no-install-recommends",
        "coolwsd",
        "coolwsd-deprecated",
        "code-brand",
    ], timeout=1800, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})

    _restart_supervisor()
    _finish_status()


def reconcile() -> None:
    wrapper_changed = _write_wrapper()
    supervisor_changed = _write_supervisor_conf()
    if supervisor_changed:
        _reread_supervisor()
    current = status.collect_status()
    if current.get("healthy"):
        if wrapper_changed or supervisor_changed:
            status.append_log("Collabora runtime configuration changed; restarting service")
            _restart_supervisor()
            time.sleep(1)
            current = status.collect_status()
        status.write_status("healthy", healthy=True, installed=True, installing=False, degraded=False, message="Collabora Online is healthy")
        return
    if current.get("installed"):
        _reread_supervisor()
        _restart_supervisor()
        status.write_status("degraded", healthy=False, installing=False, message="Collabora is installed but not healthy")
        return
    start_bootstrap_worker(force=False)


def retry_bootstrap() -> None:
    start_bootstrap_worker(force=True)


def _finish_status() -> None:
    for _ in range(12):
        current = status.collect_status()
        if current.get("healthy"):
            status.write_status("healthy", healthy=True, installed=True, installing=False, degraded=False, message="Collabora Online is healthy")
            return
        time.sleep(2)
    current = status.collect_status()
    state = "degraded" if current.get("installed") else "failed"
    status.write_status(
        state,
        healthy=False,
        installed=bool(current.get("installed")),
        installing=False,
        degraded=bool(current.get("installed")),
        message="Collabora did not become healthy yet",
    )


def _can_install() -> bool:
    return os.geteuid() == 0 and shutil.which("apt-get") is not None and shutil.which("dpkg") is not None


def _ensure_code_repo() -> None:
    KEYRING_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not KEYRING_FILE.exists():
        _run([
            "wget",
            "-O",
            str(KEYRING_FILE),
            "https://collaboraoffice.com/downloads/gpg/collaboraonline-release-keyring.gpg",
        ], timeout=300)
    SOURCES_FILE.write_text(
        "\n".join([
            "Types: deb",
            "URIs: https://www.collaboraoffice.com/repos/CollaboraOnline/CODE-deb",
            "Suites: ./",
            f"Signed-By: {KEYRING_FILE}",
            "",
        ]),
        encoding="utf-8",
    )


def _wait_for_apt_locks(timeout: int = 180) -> None:
    locks = [
        "/var/lib/dpkg/lock-frontend",
        "/var/lib/dpkg/lock",
        "/var/lib/apt/lists/lock",
        "/var/cache/apt/archives/lock",
    ]
    deadline = time.time() + timeout
    while time.time() < deadline:
        busy = False
        for lock_path in locks:
            try:
                fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.lockf(fd, fcntl.LOCK_UN)
                except OSError:
                    busy = True
                finally:
                    os.close(fd)
            except OSError:
                continue
        if not busy:
            return
        status.append_log("waiting for apt/dpkg locks")
        time.sleep(3)
    raise TimeoutError("Timed out waiting for apt/dpkg locks")


def _write_wrapper() -> bool:
    status.ensure_dirs()
    changed = _write_text_if_changed(
        WRAPPER_FILE,
        """#!/usr/bin/env bash
set -u
LOG="/a0/tmp/_office/collabora/coolwsd-wrapper.log"
LOG_DIR="$(dirname "$LOG")"
mkdir -p "$LOG_DIR" /opt/cool/cache /opt/cool/child-roots
while true; do
  if ! command -v coolwsd >/dev/null 2>&1; then
    echo "$(date -u +%FT%TZ) coolwsd missing; sleeping" >> "$LOG"
    sleep 20
    continue
  fi
  if ! id cool >/dev/null 2>&1; then
    echo "$(date -u +%FT%TZ) cool user missing; sleeping" >> "$LOG"
    sleep 20
    continue
  fi
  chown -R cool:cool "$LOG_DIR" /opt/cool/cache /opt/cool/child-roots 2>/dev/null || true
  args=(
    --o:sys_template_path=/opt/cool/systemplate
    --o:child_root_path=/opt/cool/child-roots
    --o:file_server_root_path=/usr/share/coolwsd
    --o:cache_files.path=/opt/cool/cache
    --o:ssl.enable=false
    --o:ssl.termination=false
    --o:net.listen=loopback
    --o:net.proto=IPv4
    --o:net.service_root=/office
    --o:home_mode.enable=true
  )
  if command -v runuser >/dev/null 2>&1; then
    runuser -u cool -- /usr/bin/coolwsd "${args[@]}" >> "$LOG" 2>&1 &
  else
    su -s /bin/bash cool -c 'exec /usr/bin/coolwsd "$@"' coolwsd "${args[@]}" >> "$LOG" 2>&1 &
  fi
  child=$!
  trap 'kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; exit 0' TERM INT
  wait "$child"
  code=$?
  echo "$(date -u +%FT%TZ) coolwsd exited with ${code}; restarting after backoff" >> "$LOG"
  sleep 5
done
""",
    )
    WRAPPER_FILE.chmod(0o755)
    return changed


def _write_supervisor_conf() -> bool:
    include_changed = _ensure_supervisor_include()
    if not os.access("/etc/supervisor/conf.d", os.W_OK):
        status.append_log("supervisor conf directory is not writable")
        return include_changed
    conf_changed = _write_text_if_changed(
        SUPERVISOR_CONF,
        f"""[program:{status.SUPERVISOR_PROGRAM}]
command={WRAPPER_FILE}
autostart=true
autorestart=true
startsecs=0
startretries=999999
stopsignal=TERM
stdout_logfile=/a0/tmp/_office/collabora/supervisor.log
stderr_logfile=/a0/tmp/_office/collabora/supervisor.err.log
""",
    )
    return include_changed or conf_changed


def _ensure_supervisor_include() -> bool:
    active_config = _active_supervisor_config()
    if not active_config or not active_config.exists() or not os.access(active_config, os.W_OK):
        return False
    try:
        text = active_config.read_text(encoding="utf-8")
    except OSError:
        return False
    if SUPERVISOR_INCLUDE_PATTERN in text:
        return False
    if "\n[include]\n" in f"\n{text}":
        updated = _append_to_include_files(text, SUPERVISOR_INCLUDE_PATTERN)
    else:
        updated = text.rstrip() + "\n\n[include]\nfiles = " + SUPERVISOR_INCLUDE_PATTERN + "\n"
    if updated != text:
        active_config.write_text(updated, encoding="utf-8")
        return True
    return False


def _active_supervisor_config() -> Path | None:
    cmdline = Path("/proc/1/cmdline")
    try:
        parts = [part for part in cmdline.read_text(encoding="utf-8").split("\x00") if part]
    except OSError:
        return None
    for index, part in enumerate(parts):
        if part == "-c" and index + 1 < len(parts):
            return Path(parts[index + 1])
        if part.startswith("-c") and len(part) > 2:
            return Path(part[2:])
    return Path("/etc/supervisor/supervisord.conf")


def _append_to_include_files(text: str, pattern: str) -> str:
    lines = text.splitlines()
    in_include = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_include = stripped.lower() == "[include]"
            continue
        if in_include and stripped.startswith("files"):
            separator = " " if line.rstrip().endswith("=") else " "
            lines[index] = line.rstrip() + separator + pattern
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return text.rstrip() + "\nfiles = " + pattern + "\n"


def _write_text_if_changed(path: Path, text: str) -> bool:
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")
    return True


def _reread_supervisor() -> None:
    if not shutil.which("supervisorctl"):
        return
    _run(["supervisorctl", "reread"], timeout=20, check=False)
    _run(["supervisorctl", "update", status.SUPERVISOR_PROGRAM], timeout=30, check=False)


def _restart_supervisor() -> None:
    if not shutil.which("supervisorctl"):
        return
    _run(["supervisorctl", "restart", status.SUPERVISOR_PROGRAM], timeout=30, check=False)


def _run(args: list[str], timeout: int, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    status.append_log("$ " + " ".join(args))
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=env,
        check=False,
    )
    if result.stdout:
        with status.BOOTSTRAP_LOG.open("a", encoding="utf-8") as handle:
            handle.write(result.stdout)
            if not result.stdout.endswith("\n"):
                handle.write("\n")
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed with exit {result.returncode}")
    return result
