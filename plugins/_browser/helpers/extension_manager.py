from __future__ import annotations

import json
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from helpers import files, plugins
from plugins._browser.helpers.config import PLUGIN_NAME, get_browser_config


EXTENSION_ID_RE = re.compile(r"^[a-p]{32}$")
WEB_STORE_ID_RE = re.compile(r"(?<![a-p])([a-p]{32})(?![a-p])")
WEB_STORE_DOWNLOAD_URL = (
    "https://clients2.google.com/service/update2/crx"
    "?response=redirect"
    "&prodversion=120.0.0.0"
    "&acceptformat=crx2,crx3"
    "&x=id%3D{extension_id}%26installsource%3Dondemand%26uc"
)


def get_extensions_root() -> Path:
    root = Path(files.get_abs_path("usr/browser-extensions"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def parse_chrome_web_store_extension_id(value: str) -> str:
    source = str(value or "").strip()
    if EXTENSION_ID_RE.fullmatch(source):
        return source

    match = WEB_STORE_ID_RE.search(source)
    if match:
        return match.group(1)

    raise ValueError("Enter a Chrome Web Store URL or a 32-character extension id.")


def list_browser_extensions() -> list[dict[str, Any]]:
    root = get_extensions_root()
    config = get_browser_config()
    enabled_paths = {str(Path(path).expanduser()) for path in config["extension_paths"]}
    entries: list[dict[str, Any]] = []

    for manifest_path in sorted(root.glob("**/manifest.json")):
        extension_dir = manifest_path.parent
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        extension_path = str(extension_dir)
        entries.append(
            {
                "name": manifest.get("name") or extension_dir.name,
                "version": manifest.get("version") or "",
                "path": extension_path,
                "enabled": extension_path in enabled_paths,
            }
        )

    return entries


def install_chrome_web_store_extension(source: str) -> dict[str, Any]:
    extension_id = parse_chrome_web_store_extension_id(source)
    target = get_extensions_root() / "chrome-web-store" / extension_id

    with tempfile.TemporaryDirectory(prefix="a0-browser-ext-") as tmp:
        archive_path = Path(tmp) / f"{extension_id}.crx"
        _download_crx(extension_id, archive_path)
        payload_path = Path(tmp) / f"{extension_id}.zip"
        payload_path.write_bytes(_crx_zip_payload(archive_path.read_bytes()))
        extracted_path = Path(tmp) / "extracted"
        _safe_extract_zip(payload_path, extracted_path)

        if not (extracted_path / "manifest.json").is_file():
            raise ValueError("Downloaded extension did not contain a manifest.json file.")

        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted_path, target)

    config = _enable_extension_path(target)
    manifest = _read_manifest(target)
    return {
        "ok": True,
        "id": extension_id,
        "name": manifest.get("name") or extension_id,
        "version": manifest.get("version") or "",
        "path": str(target),
        "extensions_enabled": config["extensions_enabled"],
        "extension_paths": config["extension_paths"],
    }


def _download_crx(extension_id: str, archive_path: Path) -> None:
    url = WEB_STORE_DOWNLOAD_URL.format(extension_id=extension_id)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
    if not data:
        raise ValueError("Chrome Web Store returned an empty extension package.")
    archive_path.write_bytes(data)


def _crx_zip_payload(data: bytes) -> bytes:
    if data.startswith(b"PK"):
        return data
    if data[:4] != b"Cr24":
        raise ValueError("Downloaded package is not a CRX or ZIP archive.")

    version = int.from_bytes(data[4:8], "little")
    if version == 2:
        public_key_len = int.from_bytes(data[8:12], "little")
        signature_len = int.from_bytes(data[12:16], "little")
        offset = 16 + public_key_len + signature_len
    elif version == 3:
        header_len = int.from_bytes(data[8:12], "little")
        offset = 12 + header_len
    else:
        raise ValueError(f"Unsupported CRX version: {version}.")

    payload = data[offset:]
    if not payload.startswith(b"PK"):
        raise ValueError("CRX payload did not contain a ZIP archive.")
    return payload


def _safe_extract_zip(archive_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            destination = (target_dir / member.filename).resolve()
            if not destination.is_relative_to(root):
                raise ValueError("Extension archive contains an unsafe path.")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _enable_extension_path(extension_path: Path) -> dict[str, Any]:
    config = get_browser_config()
    path = str(extension_path)
    paths = list(config["extension_paths"])
    if path not in paths:
        paths.append(path)
    config["extensions_enabled"] = True
    config["extension_paths"] = paths
    plugins.save_plugin_config(PLUGIN_NAME, "", "", config)
    return config


def _read_manifest(extension_path: Path) -> dict[str, Any]:
    manifest_path = extension_path / "manifest.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
