from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any


XPRA_SOURCE_FILE = Path("/etc/apt/sources.list.d/xpra.sources")
XPRA_KEYRING_FILE = Path("/usr/share/keyrings/xpra.asc")
XPRA_KEY_URL = "https://xpra.org/xpra.asc"
RUNTIME_PACKAGES = (
    "xpra-server",
    "xpra-client",
    "xpra-client-gtk3",
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
    "x11-xserver-utils",
    "x11-utils",
    "x11-apps",
    "xdotool",
    "xclip",
    "xauth",
    "dbus-x11",
    "python3-pil",
    "fonts-dejavu",
    "fonts-liberation",
    "fonts-crosextra-caladea",
    "fonts-crosextra-carlito",
    "fonts-noto-core",
    "fonts-noto-cjk",
    "fonts-noto-color-emoji",
)
OPTIONAL_RUNTIME_PACKAGES = (
    "xpra-client",
    "xpra-client-gtk3",
)
RETIRED_RUNTIME_PACKAGES = (
    "firefox-esr",
)


def cleanup_stale_runtime_state(force: bool = False) -> dict[str, Any]:
    """Prepare the Linux Desktop runtime and reap stale Desktop sessions."""

    installed: list[str] = []
    removed: list[str] = []
    errors: list[str] = []

    retired_packages = _installed_packages(RETIRED_RUNTIME_PACKAGES)
    if retired_packages:
        _purge_packages(removed, errors, installed_packages=retired_packages)

    _ensure_runtime_dependencies(installed, errors)
    _cleanup_desktop_sessions(errors)

    return {
        "ok": not errors,
        "skipped": False,
        "removed": removed,
        "installed": installed,
        "errors": errors,
    }


def _installed_packages(packages: tuple[str, ...]) -> list[str]:
    if not shutil.which("dpkg-query"):
        return []
    return [package for package in packages if _package_installed(package)]


def _package_installed(package: str) -> bool:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", package],
        check=False,
        text=True,
        capture_output=True,
        timeout=8,
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def _purge_packages(
    removed: list[str],
    errors: list[str],
    *,
    installed_packages: list[str] | None = None,
) -> None:
    if os.geteuid() != 0 or not shutil.which("apt-get") or not shutil.which("dpkg-query"):
        return
    installed = installed_packages if installed_packages is not None else []
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


def _ensure_runtime_dependencies(installed: list[str], errors: list[str]) -> None:
    if os.geteuid() != 0 or not shutil.which("apt-get") or not shutil.which("dpkg-query"):
        return
    missing = [package for package in RUNTIME_PACKAGES if not _package_installed(package)]
    if not missing:
        return

    if not _apt_update(errors):
        return

    required_missing, optional_missing = _split_runtime_packages(missing)
    required_xpra_missing = [package for package in required_missing if package.startswith("xpra")]
    if required_xpra_missing and not _package_candidates_available(required_xpra_missing):
        previous_error_count = len(errors)
        _ensure_xpra_repository(installed, errors)
        if len(errors) > previous_error_count or not _apt_update(errors):
            return
        missing = [package for package in RUNTIME_PACKAGES if not _package_installed(package)]
        if not missing:
            return
        required_missing, optional_missing = _split_runtime_packages(missing)

    if required_missing and not _install_runtime_packages(required_missing, installed, errors):
        return

    if optional_missing:
        optional_xpra_missing = [package for package in optional_missing if package.startswith("xpra")]
        if optional_xpra_missing and not _package_candidates_available(optional_xpra_missing):
            return
        _install_runtime_packages(optional_missing, installed, errors, optional=True)


def _split_runtime_packages(packages: list[str]) -> tuple[list[str], list[str]]:
    optional = [package for package in packages if package in OPTIONAL_RUNTIME_PACKAGES]
    required = [package for package in packages if package not in OPTIONAL_RUNTIME_PACKAGES]
    return required, optional


def _install_runtime_packages(
    packages: list[str],
    installed: list[str],
    errors: list[str],
    *,
    optional: bool = False,
) -> bool:
    result = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *packages],
        check=False,
        text=True,
        capture_output=True,
        timeout=900,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    if result.returncode == 0:
        installed.extend(packages)
        return True
    output = (result.stderr or result.stdout or "apt-get install failed").strip()
    if optional and _is_xpra_codec_dependency_gap(output):
        return False
    errors.append(output)
    return False


def _is_xpra_codec_dependency_gap(output: str) -> bool:
    normalized = output.lower()
    return "xpra-codecs" in normalized and "libvpx9" in normalized


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
    if not result.stdout.strip():
        return False
    return "Candidate: (none)" not in result.stdout


def _package_candidates_available(packages: list[str]) -> bool:
    return all(_package_candidate_available(package) for package in packages)


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

    if os_id == "kali" and arch == "amd64":
        uri = "https://xpra.org/beta"
        suite = "sid"
    elif os_id == "kali":
        uri = "https://xpra.org"
        suite = "trixie"
    elif codename in {"sid", "forky"} and arch == "amd64":
        uri = "https://xpra.org/beta"
        suite = codename
    elif codename in {"sid", "forky"}:
        uri = "https://xpra.org"
        suite = "trixie"
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
        from plugins._desktop.helpers import desktop_session

        result = desktop_session.cleanup_stale_runtime_state()
        errors.extend(str(item) for item in result.get("errors") or [])
    except Exception as exc:
        errors.append(f"Desktop cleanup failed: {exc}")
