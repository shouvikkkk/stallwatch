"""Trace parsers: Claude Code JSONL, Codex-style rollout JSONL, generic JSONL.

Design principles:

* Tolerant of unknown fields: anything not recognised is ignored, never
  fatal, as long as at least one step can be recovered.
* Fail-closed: if *no* step can be recovered from a file, the caller gets
  an empty step list plus a count of skipped lines, and the CLI reports
  UNKNOWN rather than inventing a HEALTHY verdict.
* ``tool_result`` / function-call-output records never become steps of
  their own; they are attached to the most recent matching tool call (by
  call id when available, otherwise the latest step without output yet).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import Step


@dataclass
class ParseResult:
    steps: List[Step] = field(default_factory=list)
    skipped_lines: int = 0
    warnings: List[str] = field(default_factory=list)


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _extract_tokens(obj: Any) -> Optional[int]:
    """Pull a token total out of a usage block, a tokens field, or both."""
    if not isinstance(obj, dict):
        return None
    direct = _as_int(obj.get("tokens"))
    if direct is not None:
        return direct
    total = _as_int(obj.get("total_tokens"))
    if total is not None:
        return total
    usage = obj.get("usage")
    if isinstance(usage, dict):
        total = _as_int(usage.get("total_tokens"))
        if total is not None:
            return total
        in_tok = _as_int(usage.get("input_tokens")) or 0
        out_tok = _as_int(usage.get("output_tokens")) or 0
        if in_tok or out_tok:
            return in_tok + out_tok
    return None


def _parse_args(raw: Any) -> Dict[str, Any]:
    """Arguments may be a dict, a JSON string, or a bare command string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return {"command": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"command": raw}
    if raw is None:
        return {}
    return {"value": raw}


def _content_text(value: Any) -> str:
    """Flatten a tool-result content value (string, or list of blocks)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", item.get("content", ""))))
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        return str(value.get("text", value.get("content", "")))
    return str(value)


def _tool_result_blocks(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return tool_result entries found anywhere in a record, if any."""
    blocks: List[Dict[str, Any]] = []
    content = None
    if isinstance(record.get("message"), dict):
        content = record["message"].get("content")
    if content is None:
        content = record.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") in (
                "tool_result",
                "function_call_output",
            ):
                blocks.append(item)
    if record.get("type") in ("tool_result", "function_call_output"):
        blocks.append(record)
    item = record.get("item")
    if isinstance(item, dict) and item.get("type") in (
        "tool_result",
        "function_call_output",
    ):
        blocks.append(item)
    if isinstance(record.get("tool_result"), dict):
        blocks.append(record["tool_result"])
    if isinstance(record.get("function_call_output"), dict):
        blocks.append(record["function_call_output"])
    return blocks


def _tool_use_blocks(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return tool_use / function_call entries found in a record, if any."""
    blocks: List[Dict[str, Any]] = []
    content = None
    if isinstance(record.get("message"), dict):
        content = record["message"].get("content")
    if content is None:
        content = record.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") in (
                "tool_use",
                "function_call",
            ):
                blocks.append(item)
    item = record.get("item")
    if isinstance(item, dict) and item.get("type") == "function_call":
        blocks.append(item)
    if record.get("type") == "function_call":
        blocks.append(record)
    tool_calls = record.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                blocks.append(call)
    return blocks


def _step_from_tool_use(block: Dict[str, Any], tokens: Optional[int]) -> Optional[Step]:
    """Build a step from a tool_use / function_call / tool_calls entry."""
    name = block.get("name")
    function = block.get("function")
    if name is None and isinstance(function, dict):
        name = function.get("name")
    if name is None:
        return None
    raw_args = block.get("input", block.get("arguments"))
    if raw_args is None and isinstance(function, dict):
        raw_args = function.get("arguments")
    if raw_args is None:
        raw_args = block.get("args", block.get("parameters"))
    step = Step(
        index=0,
        tool=str(name),
        args=_parse_args(raw_args),
        tokens=_extract_tokens(block) or tokens,
        call_id=(
            str(block.get("id") or block.get("tool_use_id") or block.get("call_id"))
            if (block.get("id") or block.get("tool_use_id") or block.get("call_id"))
            else None
        ),
    )
    return step


def _step_from_generic(record: Dict[str, Any]) -> Optional[Step]:
    """Build a step from a flat record (generic JSONL, Codex flat rows)."""
    name = record.get("tool") or record.get("function") or record.get("action")
    if name is not None and not isinstance(name, str):
        name = str(name)
    if name is None:
        return None
    # A bare "name" field only counts if the record also carries call data,
    # so that unrelated records with a name field are not mistaken for steps.
    if record.get("tool") is None and record.get("function") is None and record.get("action") is None:
        if not any(
            k in record for k in ("args", "input", "arguments", "parameters", "output", "error")
        ):
            return None
    raw_args = None
    for key in ("args", "input", "arguments", "parameters"):
        if key in record:
            raw_args = record[key]
            break
    error = record.get("error") or record.get("error_text")
    if record.get("is_error") and error is None:
        error = _content_text(record.get("output") or record.get("result"))
    step = Step(
        index=0,
        tool=str(name),
        args=_parse_args(raw_args),
        output=_content_text(record.get("output") or record.get("result") or ""),
        error=str(error) if error is not None else None,
        tokens=_extract_tokens(record),
        call_id=(
            str(record.get("id") or record.get("call_id"))
            if (record.get("id") or record.get("call_id"))
            else None
        ),
    )
    return step


def _attach_result(
    steps: List[Step], block: Dict[str, Any]
) -> bool:
    """Attach a result block to its call; True if it was attached."""
    target_id = block.get("tool_use_id") or block.get("call_id") or block.get("id")
    target: Optional[Step] = None
    if target_id is not None:
        for step in reversed(steps):
            if step.call_id == str(target_id):
                target = step
                break
    if target is None:
        for step in reversed(steps):
            if not step.output and not step.has_error:
                target = step
                break
    if target is None:
        return False
    text = _content_text(block.get("content") or block.get("output") or block.get("result"))
    if block.get("is_error") or block.get("error"):
        target.error = text or str(block.get("error"))
    else:
        if text:
            target.output = text
    tokens = _extract_tokens(block)
    if tokens is not None and target.tokens is None:
        target.tokens = tokens
    return True


def _handle_record(record: Any, result: ParseResult) -> None:
    """Process one decoded JSON value, appending steps or counting skips."""
    if isinstance(record, list):
        for item in record:
            _handle_record(item, result)
        return
    if not isinstance(record, dict):
        result.skipped_lines += 1
        return
    result_blocks = _tool_result_blocks(record)
    use_blocks = _tool_use_blocks(record)
    if result_blocks and not use_blocks:
        attached = False
        for block in result_blocks:
            attached = _attach_result(result.steps, block) or attached
        if attached:
            return
        # An orphan result with no call to attach to is not a step.
        result.skipped_lines += 1
        return
    tokens = _extract_tokens(record)
    if isinstance(record.get("message"), dict):
        tokens = tokens or _extract_tokens(record["message"])
    made = False
    for block in use_blocks:
        step = _step_from_tool_use(block, tokens)
        if step is not None:
            step.index = len(result.steps) + 1
            result.steps.append(step)
            made = True
    if made:
        # A record carrying both a use and its result attaches the result
        # to the step just created.
        for block in result_blocks:
            _attach_result(result.steps, block)
        return
    step = _step_from_generic(record)
    if step is not None:
        step.index = len(result.steps) + 1
        result.steps.append(step)
        return
    result.skipped_lines += 1


def parse_trace_text(text: str) -> ParseResult:
    """Parse a whole trace file's text into steps."""
    result = ParseResult()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except ValueError:
            result.skipped_lines += 1
            continue
        _handle_record(record, result)
    return result


def parse_trace_file(path: str) -> ParseResult:
    """Parse a trace file from disk."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parse_trace_text(handle.read())
