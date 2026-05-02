from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any


SOFFICE_BINARIES = ("soffice", "libreoffice")
CONVERT_TIMEOUT_SECONDS = 45


def find_soffice() -> str:
    for name in SOFFICE_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    return ""


def collect_status() -> dict[str, Any]:
    soffice = find_soffice()
    status = {
        "ok": True,
        "state": "healthy" if soffice else "missing",
        "healthy": bool(soffice),
        "soffice": soffice,
        "libreofficekit": _libreofficekit_available(),
        "message": "LibreOffice is available." if soffice else "LibreOffice is not installed in this runtime.",
    }
    try:
        from plugins._office.helpers import libreoffice_desktop

        status["desktop"] = libreoffice_desktop.collect_desktop_status()
    except Exception as exc:
        status["desktop"] = {"ok": False, "healthy": False, "error": str(exc)}
    return status


@lru_cache(maxsize=1)
def _libreofficekit_available() -> bool:
    system_dist_packages = Path("/usr/lib/python3/dist-packages")
    if system_dist_packages.exists() and str(system_dist_packages) not in sys.path:
        sys.path.append(str(system_dist_packages))
    try:
        import gi  # type: ignore

        gi.require_version("LOKDocView", "0.1")
        return True
    except Exception:
        return _lokdocview_typelib_available()


def _lokdocview_typelib_available() -> bool:
    candidates = [
        Path("/usr/lib/x86_64-linux-gnu/girepository-1.0/LOKDocView-0.1.typelib"),
        Path("/usr/lib/aarch64-linux-gnu/girepository-1.0/LOKDocView-0.1.typelib"),
        Path("/usr/share/gir-1.0/LOKDocView-0.1.gir"),
    ]
    return any(path.exists() for path in candidates)


def validate_docx(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {"ok": False, "error": f"File not found: {source}"}
    try:
        with zipfile.ZipFile(source) as archive:
            archive.getinfo("[Content_Types].xml")
            archive.getinfo("word/document.xml")
    except Exception as exc:
        return {"ok": False, "error": f"DOCX package validation failed: {exc}"}

    soffice = find_soffice()
    if not soffice:
        return {"ok": True, "warning": "LibreOffice binary was not available; package validation only."}

    with tempfile.TemporaryDirectory(prefix="a0-office-validate-") as temp_dir:
        result = _run_soffice(
            soffice,
            [
                "--headless",
                "--safe-mode",
                "--convert-to",
                "pdf",
                "--outdir",
                temp_dir,
                str(source),
            ],
            timeout=CONVERT_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return {"ok": False, "error": _format_process_error(result)}
        return {"ok": True}


def convert_document(path: str | Path, target_format: str, output_dir: str | Path | None = None) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {"ok": False, "error": f"File not found: {source}"}
    soffice = find_soffice()
    if not soffice:
        return {"ok": False, "error": "LibreOffice is not installed in this runtime."}

    target_format = str(target_format or "").lower().strip().lstrip(".")
    if not target_format:
        return {"ok": False, "error": "target_format is required."}

    destination_dir = Path(output_dir) if output_dir else source.parent
    destination_dir.mkdir(parents=True, exist_ok=True)
    before = {item.name for item in destination_dir.iterdir()} if destination_dir.exists() else set()
    result = _run_soffice(
        soffice,
        [
            "--headless",
            "--safe-mode",
            "--convert-to",
            target_format,
            "--outdir",
            str(destination_dir),
            str(source),
        ],
        timeout=CONVERT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return {"ok": False, "error": _format_process_error(result)}

    expected = destination_dir / f"{source.stem}.{target_format}"
    if expected.exists():
        return {"ok": True, "path": str(expected)}

    created = [item for item in destination_dir.iterdir() if item.name not in before]
    if created:
        return {"ok": True, "path": str(created[0])}
    return {"ok": False, "error": "LibreOffice completed without producing an output file."}


def _run_soffice(soffice: str, args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HOME": os.environ.get("HOME") or "/tmp",
        "SAL_USE_VCLPLUGIN": os.environ.get("SAL_USE_VCLPLUGIN") or "gen",
    }
    return subprocess.run(
        [soffice, *args],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def _format_process_error(result: subprocess.CompletedProcess[str]) -> str:
    details = (result.stderr or result.stdout or "").strip()
    if details:
        return f"LibreOffice exited with {result.returncode}: {details}"
    return f"LibreOffice exited with {result.returncode}."
