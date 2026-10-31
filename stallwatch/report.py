"""Analysis + verdicts + text/JSON rendering.

Verdict rules (documented, deterministic):

* No parseable steps -> UNKNOWN (fail closed).
* Any ``stall``-severity finding -> STALLED.
* Any ``degraded`` finding, or a progress score below 0.50 -> DEGRADED.
* Otherwise -> HEALTHY.

Wasted tokens are the sum of usage tokens from the first stall step
onward. When no stall step exists, wasted tokens are 0. When a stall
exists but no step from that point onward carries usage data, wasted
tokens are reported as unknown (``None`` / ``"unknown"``) -- never
estimated from step counts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .detectors import all_findings, compute_progress
from .models import Finding, Step

VERDICT_EXIT_CODES = {"HEALTHY": 0, "DEGRADED": 1, "STALLED": 2, "UNKNOWN": 3}
LOW_PROGRESS_THRESHOLD = 0.50


@dataclass
class Analysis:
    verdict: str
    total_steps: int
    progress_score: float
    progress_steps: int
    findings: List[Finding] = field(default_factory=list)
    first_stall_step: Optional[int] = None
    wasted_tokens: Optional[int] = None
    wasted_tokens_unknown: bool = False
    skipped_lines: int = 0

    @property
    def exit_code(self) -> int:
        return VERDICT_EXIT_CODES[self.verdict]


def analyze(
    steps: List[Step],
    min_repeat: int = 3,
    window: int = 8,
    skipped_lines: int = 0,
) -> Analysis:
    if not steps:
        return Analysis(
            verdict="UNKNOWN",
            total_steps=0,
            progress_score=0.0,
            progress_steps=0,
            wasted_tokens=None,
            wasted_tokens_unknown=True,
            skipped_lines=skipped_lines,
        )
    findings = all_findings(steps, min_repeat=min_repeat, window=window)
    score, progress_steps = compute_progress(steps)
    stall_findings = [f for f in findings if f.severity == "stall"]
    first_stall = min((f.start_step for f in stall_findings), default=None)
    wasted: Optional[int] = None
    wasted_unknown = False
    if first_stall is not None:
        tail = [s for s in steps if s.index >= first_stall]
        known = [s.tokens for s in tail if s.tokens is not None]
        if known:
            wasted = sum(known)
        else:
            wasted_unknown = True
    else:
        wasted = 0
    if stall_findings:
        verdict = "STALLED"
    elif findings or score < LOW_PROGRESS_THRESHOLD:
        verdict = "DEGRADED"
    else:
        verdict = "HEALTHY"
    return Analysis(
        verdict=verdict,
        total_steps=len(steps),
        progress_score=score,
        progress_steps=progress_steps,
        findings=findings,
        first_stall_step=first_stall,
        wasted_tokens=wasted,
        wasted_tokens_unknown=wasted_unknown,
        skipped_lines=skipped_lines,
    )


def finding_to_dict(finding: Finding) -> Dict[str, Any]:
    return {
        "kind": finding.kind,
        "start_step": finding.start_step,
        "end_step": finding.end_step,
        "detail": finding.detail,
        "severity": finding.severity,
        "extra": finding.extra,
    }


def analysis_to_dict(analysis: Analysis) -> Dict[str, Any]:
    return {
        "verdict": analysis.verdict,
        "exit_code": analysis.exit_code,
        "total_steps": analysis.total_steps,
        "progress_score": round(analysis.progress_score, 3),
        "progress_steps": analysis.progress_steps,
        "first_stall_step": analysis.first_stall_step,
        "wasted_tokens": analysis.wasted_tokens,
        "wasted_tokens_unknown": analysis.wasted_tokens_unknown,
        "skipped_lines": analysis.skipped_lines,
        "findings": [finding_to_dict(f) for f in analysis.findings],
    }


def render_text(analysis: Analysis) -> str:
    lines = [f"Verdict: {analysis.verdict} (exit {analysis.exit_code})"]
    lines.append(
        f"Steps: {analysis.total_steps}  "
        f"Progress: {analysis.progress_steps}/{analysis.total_steps} "
        f"({analysis.progress_score:.0%})"
    )
    if analysis.first_stall_step is not None:
        lines.append(f"First stall step: {analysis.first_stall_step}")
    if analysis.wasted_tokens_unknown:
        lines.append("Wasted tokens: unknown (no usage data from stall onward)")
    else:
        lines.append(f"Wasted tokens: {analysis.wasted_tokens}")
    if analysis.skipped_lines:
        lines.append(f"Skipped unparseable lines: {analysis.skipped_lines}")
    if not analysis.findings:
        if analysis.verdict == "UNKNOWN":
            lines.append("No parseable steps found in the trace.")
        else:
            lines.append("No findings.")
    for finding in analysis.findings:
        lines.append(
            f"- [{finding.severity.upper()}] {finding.kind} "
            f"(steps {finding.start_step}-{finding.end_step}): {finding.detail}"
        )
    return "\n".join(lines) + "\n"


def render_json(analysis: Analysis) -> str:
    return json.dumps(analysis_to_dict(analysis), indent=2) + "\n"
