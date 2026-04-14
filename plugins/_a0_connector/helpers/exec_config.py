from __future__ import annotations

from typing import Any

_TIMEOUT_KEYS = (
    "first_output_timeout",
    "between_output_timeout",
    "max_exec_timeout",
    "dialog_timeout",
)

_DEFAULT_CODE_EXEC_TIMEOUTS = {
    "first_output_timeout": 30,
    "between_output_timeout": 15,
    "max_exec_timeout": 180,
    "dialog_timeout": 5,
}
_DEFAULT_OUTPUT_TIMEOUTS = {
    "first_output_timeout": 90,
    "between_output_timeout": 45,
    "max_exec_timeout": 300,
    "dialog_timeout": 5,
}
_DEFAULT_PROMPT_PATTERNS = [
    r"(\(venv\)).+[$#] ?$",
    r"root@[^:]+:[^#]+# ?$",
    r"[a-zA-Z0-9_.-]+@[^:]+:[^$#]+[$#] ?$",
    r"\(?.*\)?\s*PS\s+[^>]+> ?$",
]
_DEFAULT_DIALOG_PATTERNS = [
    r"Y/N",
    r"yes/no",
    r":\s*$",
    r"\?\s*$",
]


def _coerce_timeout_group(raw: Any, defaults: dict[str, int]) -> dict[str, int]:
    group = raw if isinstance(raw, dict) else {}
    result: dict[str, int] = {}
    for key in _TIMEOUT_KEYS:
        value = group.get(key, defaults[key])
        try:
            result[key] = int(value)
        except (TypeError, ValueError):
            result[key] = defaults[key]
    return result


def _pattern_lines(raw: Any, defaults: list[str]) -> list[str]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        values = raw.splitlines()
    else:
        values = defaults

    patterns = [str(value).strip() for value in values if str(value).strip()]
    return patterns or list(defaults)


def build_exec_config() -> dict[str, Any]:
    from helpers import plugins

    try:
        config = plugins.get_plugin_config("_code_execution") or {}
    except Exception:
        config = {}

    return {
        "version": 1,
        "code_exec_timeouts": _coerce_timeout_group(
            config.get("code_exec_timeouts") or {
                key: config.get(f"code_exec_{key}")
                for key in _TIMEOUT_KEYS
                if f"code_exec_{key}" in config
            },
            _DEFAULT_CODE_EXEC_TIMEOUTS,
        ),
        "output_timeouts": _coerce_timeout_group(
            config.get("output_timeouts") or {
                key: config.get(f"output_{key}")
                for key in _TIMEOUT_KEYS
                if f"output_{key}" in config
            },
            _DEFAULT_OUTPUT_TIMEOUTS,
        ),
        "prompt_patterns": _pattern_lines(
            config.get("prompt_patterns"),
            _DEFAULT_PROMPT_PATTERNS,
        ),
        "dialog_patterns": _pattern_lines(
            config.get("dialog_patterns"),
            _DEFAULT_DIALOG_PATTERNS,
        ),
    }
