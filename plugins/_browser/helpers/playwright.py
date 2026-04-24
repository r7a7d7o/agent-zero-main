import os
import subprocess
from pathlib import Path

from helpers import files

HEADLESS_SHELL_PATTERNS = (
    "chromium_headless_shell-*/chrome-*/headless_shell",
    "chromium_headless_shell-*/chrome-*/headless_shell.exe",
)

FULL_CHROMIUM_PATTERNS = (
    "chromium-*/chrome-linux/chrome",
    "chromium-*/chrome-win/chrome.exe",
)


def get_playwright_cache_dir() -> str:
    return files.get_abs_path("tmp/playwright")


def configure_playwright_env() -> str:
    cache_dir = get_playwright_cache_dir()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = cache_dir
    return cache_dir


def get_playwright_binary(*, full_browser: bool = False) -> Path | None:
    cache_dir = Path(get_playwright_cache_dir())
    patterns = FULL_CHROMIUM_PATTERNS if full_browser else (HEADLESS_SHELL_PATTERNS + FULL_CHROMIUM_PATTERNS)
    for pattern in patterns:
        binary = next(cache_dir.glob(pattern), None)
        if binary and binary.exists():
            return binary
    return None


def ensure_playwright_binary(*, full_browser: bool = False) -> Path:
    binary = get_playwright_binary(full_browser=full_browser)
    if binary:
        return binary

    cache_dir = configure_playwright_env()
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = cache_dir
    install_command = ["playwright", "install", "chromium"]
    if not full_browser:
        install_command.append("--only-shell")
    subprocess.check_call(
        install_command,
        env=env,
    )

    binary = get_playwright_binary(full_browser=full_browser)
    if not binary:
        raise RuntimeError("Playwright Chromium binary not found after installation")
    return binary
