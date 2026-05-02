from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APT_SOURCE_FILE = Path("/etc/apt/sources.list.d/collaboraonline.sources")
APT_KEYRING_FILE = Path("/etc/apt/keyrings/collaboraonline-release-keyring.gpg")
XPRA_SOURCE_FILE = Path("/etc/apt/sources.list.d/xpra.sources")
XPRA_KEYRING_FILE = Path("/usr/share/keyrings/xpra.asc")
XPRA_KEY_URL = "https://xpra.org/xpra.asc"
SUPERVISOR_FILE = Path("/etc/supervisor/conf.d/a0_office_collabora.conf")
RUNTIME_DIRS = [
    Path("/a0/tmp/_office/collabora"),
    Path("/a0/usr/plugins/_office/collabora"),
    PROJECT_ROOT / "tmp" / "_office" / "collabora",
    PROJECT_ROOT / "usr" / "plugins" / "_office" / "collabora",
]
PACKAGES = (
    "coolwsd",
    "coolwsd-deprecated",
    "code-brand",
    "collaboraoffice",
    "collaboraofficebasis-calc",
    "collaboraofficebasis-draw",
    "collaboraofficebasis-en-us",
    "collaboraofficebasis-extension-pdf-import",
    "collaboraofficebasis-graphicfilter",
    "collaboraofficebasis-images",
    "collaboraofficebasis-impress",
    "collaboraofficebasis-math",
    "collaboraofficebasis-ooolinguistic",
    "collaboraofficebasis-writer",
)
RUNTIME_PACKAGES = (
    "libreoffice-core",
    "libreoffice-writer",
    "libreoffice-calc",
    "libreoffice-impress",
    "libreoffice-gtk3",
    "python3-uno",
    "xpra",
    "xpra-x11",
    "xpra-html5",
    "xfce4-session",
    "xfwm4",
    "xfce4-panel",
    "xfdesktop4",
    "xfce4-settings",
    "thunar",
    "gvfs",
    "libglib2.0-bin",
    "xfce4-terminal",
    "pulseaudio",
    "pulseaudio-utils",
    "x11-xserver-utils",
    "xdotool",
    "xauth",
    "dbus-x11",
    "fonts-dejavu",
    "fonts-liberation",
    "fonts-crosextra-caladea",
    "fonts-crosextra-carlito",
    "fonts-noto-core",
    "fonts-noto-cjk",
    "fonts-noto-color-emoji",
)
RETIRED_RUNTIME_PACKAGES = (
    "firefox-esr",
)
CLEANUP_MARKER = PROJECT_ROOT / "usr" / "plugins" / "_office" / "stale-cleanup-v2.done"


def cleanup_stale_runtime_state(force: bool = False) -> dict[str, Any]:
    """Prepare the LibreOffice runtime and remove retired office state.

    The hook is intentionally idempotent: existing dependencies, missing stale
    files, packages, and processes count as already clean. It is safe to call
    during startup and self-update.
    """

    removed: list[str] = []
    installed: list[str] = []
    errors: list[str] = []

    stale_paths = [
        path
        for path in [APT_SOURCE_FILE, APT_KEYRING_FILE, SUPERVISOR_FILE, *RUNTIME_DIRS]
        if path.exists() or path.is_symlink()
    ]
    stale_packages = _installed_packages(PACKAGES)
    cleanup_needed = force or not CLEANUP_MARKER.exists() or bool(stale_paths or stale_packages)

    if cleanup_needed:
        _kill_old_processes(errors)

        for path in [APT_SOURCE_FILE, APT_KEYRING_FILE, SUPERVISOR_FILE, *RUNTIME_DIRS]:
            try:
                if _remove_path(path):
                    removed.append(str(path))
            except Exception as exc:
                errors.append(f"{path}: {exc}")

        _purge_packages(removed, errors, installed_packages=stale_packages)

        try:
            CLEANUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
            CLEANUP_MARKER.write_text("ok\n", encoding="utf-8")
        except Exception as exc:
            errors.append(f"{CLEANUP_MARKER}: {exc}")

    retired_packages = [
        package
        for package in _installed_packages(RETIRED_RUNTIME_PACKAGES)
        if package not in stale_packages
    ]
    if retired_packages:
        _purge_packages(removed, errors, installed_packages=retired_packages)

    _ensure_runtime_dependencies(installed, errors)
    _cleanup_desktop_sessions(errors)

    return {
        "ok": not errors,
        "skipped": not cleanup_needed,
        "removed": removed,
        "installed": installed,
        "errors": errors,
    }


def _remove_path(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return True
    if path.exists():
        shutil.rmtree(path)
        return True
    return False


def _kill_old_processes(errors: list[str]) -> None:
    if not shutil.which("pkill"):
        return
    result = subprocess.run(
        ["pkill", "-f", "coolwsd"],
        check=False,
        text=True,
        capture_output=True,
        timeout=8,
    )
    if result.returncode not in {0, 1}:
        errors.append((result.stderr or result.stdout or "pkill coolwsd failed").strip())


def _installed_packages(packages: tuple[str, ...]) -> list[str]:
    if not shutil.which("dpkg-query"):
        return []
    return [package for package in packages if _package_installed(package)]


def _purge_packages(
    removed: list[str],
    errors: list[str],
    *,
    installed_packages: list[str] | None = None,
) -> None:
    if os.geteuid() != 0 or not shutil.which("apt-get") or not shutil.which("dpkg-query"):
        return
    installed = installed_packages if installed_packages is not None else _installed_packages(PACKAGES)
    if not installed:
        return
    result = subprocess.run(
        ["apt-get", "purge", "-y", *installed],
        check=False,
        text=True,
        capture_output=True,
        timeout=180,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    if result.returncode == 0:
        removed.extend(installed)
        return
    errors.append((result.stderr or result.stdout or "apt-get purge failed").strip())


def _package_installed(package: str) -> bool:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", package],
        check=False,
        text=True,
        capture_output=True,
        timeout=8,
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def _ensure_runtime_dependencies(installed: list[str], errors: list[str]) -> None:
    if os.geteuid() != 0 or not shutil.which("apt-get") or not shutil.which("dpkg-query"):
        return
    missing = [package for package in RUNTIME_PACKAGES if not _package_installed(package)]
    if not missing:
        return

    if not _apt_update(errors):
        return

    if "xpra" in missing and not _package_candidate_available("xpra"):
        previous_error_count = len(errors)
        _ensure_xpra_repository(installed, errors)
        if len(errors) > previous_error_count or not _apt_update(errors):
            return
        missing = [package for package in RUNTIME_PACKAGES if not _package_installed(package)]
        if not missing:
            return

    result = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *missing],
        check=False,
        text=True,
        capture_output=True,
        timeout=900,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    if result.returncode == 0:
        installed.extend(missing)
        return
    errors.append((result.stderr or result.stdout or "apt-get install failed").strip())


def _apt_update(errors: list[str]) -> bool:
    result = subprocess.run(
        ["apt-get", "update"],
        check=False,
        text=True,
        capture_output=True,
        timeout=300,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    if result.returncode == 0:
        return True
    errors.append((result.stderr or result.stdout or "apt-get update failed").strip())
    return False


def _package_candidate_available(package: str) -> bool:
    if not shutil.which("apt-cache"):
        return True
    result = subprocess.run(
        ["apt-cache", "policy", package],
        check=False,
        text=True,
        capture_output=True,
        timeout=15,
    )
    if result.returncode != 0:
        return True
    return "Candidate: (none)" not in result.stdout


def _ensure_xpra_repository(installed: list[str], errors: list[str]) -> None:
    if not _package_installed("ca-certificates"):
        result = subprocess.run(
            ["apt-get", "install", "-y", "--no-install-recommends", "ca-certificates"],
            check=False,
            text=True,
            capture_output=True,
            timeout=180,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
        if result.returncode != 0:
            errors.append((result.stderr or result.stdout or "apt-get install ca-certificates failed").strip())
            return
        installed.append("ca-certificates")

    try:
        key = _download(XPRA_KEY_URL)
        XPRA_KEYRING_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not XPRA_KEYRING_FILE.exists() or XPRA_KEYRING_FILE.read_bytes() != key:
            XPRA_KEYRING_FILE.write_bytes(key)

        XPRA_SOURCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        source = _xpra_repository_source()
        if not XPRA_SOURCE_FILE.exists() or XPRA_SOURCE_FILE.read_text(encoding="utf-8") != source:
            XPRA_SOURCE_FILE.write_text(source, encoding="utf-8")
    except Exception as exc:
        errors.append(f"Xpra repository setup failed: {exc}")


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=45) as response:
        return response.read()


def _xpra_repository_source() -> str:
    os_release = _read_os_release()
    os_id = os_release.get("ID", "")
    codename = os_release.get("VERSION_CODENAME", "")
    arch = _dpkg_architecture()

    if os_id == "kali":
        uri = "https://xpra.org/beta"
        suite = "sid"
    elif codename in {"sid", "forky"}:
        uri = "https://xpra.org/beta"
        suite = codename
    else:
        uri = "https://xpra.org"
        suite = codename or "trixie"

    return (
        f"Types: deb\n"
        f"URIs: {uri}\n"
        f"Suites: {suite}\n"
        f"Components: main\n"
        f"Signed-By: {XPRA_KEYRING_FILE}\n"
        f"Architectures: {arch}\n"
    )


def _read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def _dpkg_architecture() -> str:
    result = subprocess.run(
        ["dpkg", "--print-architecture"],
        check=False,
        text=True,
        capture_output=True,
        timeout=8,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "amd64"


def _cleanup_desktop_sessions(errors: list[str]) -> None:
    try:
        from plugins._office.helpers import libreoffice_desktop

        result = libreoffice_desktop.cleanup_stale_runtime_state()
        errors.extend(str(item) for item in result.get("errors") or [])
    except Exception as exc:
        errors.append(f"LibreOffice desktop cleanup failed: {exc}")
