"""Shared data types for stallwatch.

A :class:`Step` is one tool call in a finished agent run trace, normalised
out of whichever trace format the parser saw. A :class:`Finding` is one
detector's verdict about a slice of those steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Step:
    """One tool call, in trace order."""

    index: int  # 1-based position among parsed steps
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    output: str = ""
    error: Optional[str] = None
    tokens: Optional[int] = None
    call_id: Optional[str] = None

    @property
    def has_error(self) -> bool:
        return self.error is not None and str(self.error).strip() != ""


@dataclass
class Finding:
    """One detector result."""

    kind: str  # e.g. "exact_repeat", "cycle", "reread_without_write"
    start_step: int  # 1-based, inclusive
    end_step: int  # 1-based, inclusive
    detail: str
    severity: str = "degraded"  # "stall" (hard loop) or "degraded" (warning)
    extra: Dict[str, Any] = field(default_factory=dict)
