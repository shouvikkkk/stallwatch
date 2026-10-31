"""Normalisation and tool classification helpers.

All comparisons in the detectors go through here so that two calls that
differ only in key order, whitespace, or capitalisation count as the same
call, while genuinely different arguments do not.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from .models import Step

# Tools that change state when they succeed. Compared case-insensitively.
STATE_CHANGING_TOOLS = {
    "write",
    "edit",
    "notebookedit",
    "create",
    "create_file",
    "delete",
    "remove",
    "move",
    "rename",
    "mkdir",
    "apply_patch",
    "commit",
    "push",
    "pull_request",
    "create_pr",
    "create_pull_request",
    "merge",
    "install",
    "apply",
    "patch",
    "save",
    "upload",
}

WRITE_TOOLS = {
    "write",
    "edit",
    "notebookedit",
    "create",
    "create_file",
    "apply_patch",
    "save",
}

READ_TOOLS = {
    "read",
    "get",
    "view",
    "cat",
    "open",
    "fetch",
    "glob",
    "grep",
    "search",
}

# Bash fragments that indicate the command mutates something, rather than
# merely inspecting it. Checked against the command string, lower-cased.
_MUTATING_BASH_PATTERNS = [
    re.compile(r">>?\s*[^\s=&]"),  # redirection to a file (not >& or =>)
    re.compile(r"\brm\s"),
    re.compile(r"\brmdir\b"),
    re.compile(r"\bmkdir\b"),
    re.compile(r"\bmv\s"),
    re.compile(r"\bcp\s"),
    re.compile(r"\btouch\b"),
    re.compile(r"\bsed\s+[^|]*-i\b"),
    re.compile(r"\bgit\s+(commit|push|add|mv|rm|checkout\s+-b|apply)\b"),
    re.compile(r"\bchmod\b"),
    re.compile(r"\bchown\b"),
    re.compile(r"\bnpm\s+install\b"),
    re.compile(r"\bpip\s+install\b"),
    re.compile(r"\btee\s"),
    re.compile(r"\btruncate\b"),
    re.compile(r"\bcp\s"),
]

_PATH_ARG_KEYS = (
    "file_path",
    "path",
    "file",
    "filename",
    "filepath",
    "notebook_path",
    "target_path",
    "target",
)


def normalize_text(value: Any) -> str:
    """Collapse whitespace and strip; non-strings become their str form."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_error(value: Any) -> str:
    """Normalise an error message for recurrence grouping.

    Lower-cased, whitespace-collapsed, with digit runs replaced by ``#`` so
    that "retry 3 failed" and "retry 4 failed" group together, while
    genuinely different messages stay apart.
    """
    text = normalize_text(value).lower()
    text = re.sub(r"0x[0-9a-f]+", "#", text)
    text = re.sub(r"\d+", "#", text)
    return text.strip(" .:")


def normalize_args(value: Any) -> Any:
    """Recursively normalise an args value for equality comparison."""
    if isinstance(value, dict):
        return {k: normalize_args(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [normalize_args(v) for v in value]
    if isinstance(value, str):
        return normalize_text(value)
    return value


def args_key(step: Step) -> str:
    """Stable identity of a call: tool (lower-cased) + normalised args."""
    return json.dumps(
        [step.tool.lower(), normalize_args(step.args)],
        sort_keys=True,
        default=str,
    )


def tool_name(step: Step) -> str:
    return step.tool.lower()


def is_mutating_bash(step: Step) -> bool:
    if tool_name(step) not in {"bash", "shell", "run", "exec", "command"}:
        return False
    command = ""
    if isinstance(step.args, dict):
        for key in ("command", "cmd", "script", "input"):
            if key in step.args:
                command = str(step.args[key])
                break
        else:
            command = json.dumps(step.args, default=str)
    command = command.lower()
    return any(p.search(command) for p in _MUTATING_BASH_PATTERNS)


def is_state_changing(step: Step) -> bool:
    """True if the step writes, edits, creates, deletes, or mutates state."""
    if tool_name(step) in STATE_CHANGING_TOOLS:
        return True
    return is_mutating_bash(step)


def is_write(step: Step) -> bool:
    if tool_name(step) in WRITE_TOOLS:
        return True
    return is_mutating_bash(step)


def is_read(step: Step) -> bool:
    return tool_name(step) in READ_TOOLS


def path_from_args(args: Any) -> Optional[str]:
    """Extract a file path from a step's args, if one is named."""
    if isinstance(args, dict):
        for key in _PATH_ARG_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(args, str) and args.strip():
        return args.strip()
    return None


def bash_mentions_path(step: Step, path: str) -> bool:
    """True if a mutating bash step's command text mentions ``path``."""
    if not is_mutating_bash(step):
        return False
    command = json.dumps(step.args, default=str).lower()
    return path.lower() in command
