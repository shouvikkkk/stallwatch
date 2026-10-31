"""Stall detectors. Every function here is pure, tested, and stdlib-only.

Each detector returns a list of :class:`Finding` objects. Severity
``"stall"`` means a hard loop / dead stagnation; ``"degraded"`` means a
warning pattern (repeated errors, a re-read, a legitimate retry loop) that
on its own does not prove the agent was stuck.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .models import Finding, Step
from .normalize import (
    args_key,
    bash_mentions_path,
    is_read,
    is_state_changing,
    is_write,
    normalize_error,
    normalize_text,
    path_from_args,
    tool_name,
)


def detect_exact_repeat(
    steps: List[Step], min_repeat: int = 3, window: int = 10
) -> List[Finding]:
    """Same tool + normalised args repeated >= ``min_repeat`` times.

    Covers two shapes: consecutive repeats, and repeats scattered inside a
    sliding window of ``window`` steps (e.g. Read X, Read Y, Read X, Read X).
    One finding is emitted per run / per key, never one per step.
    """
    findings: List[Finding] = []
    if min_repeat < 2 or not steps:
        return findings

    # Consecutive runs.
    run_key: Optional[str] = None
    run_start = 0
    run_count = 0
    run_step: Optional[Step] = None

    def flush_consecutive(end_index: int) -> None:
        nonlocal run_key, run_count, run_step
        if run_key is not None and run_count >= min_repeat and run_step is not None:
            findings.append(
                Finding(
                    kind="exact_repeat",
                    start_step=steps[run_start].index,
                    end_step=steps[end_index - 1].index,
                    detail=(
                        f"Call repeated {run_count} times consecutively: "
                        f"{run_step.tool} {normalize_text(run_step.args)}"
                    ),
                    severity="stall",
                    extra={
                        "tool": run_step.tool,
                        "count": run_count,
                        "consecutive": True,
                    },
                )
            )

    for i, step in enumerate(steps):
        key = args_key(step)
        if key == run_key:
            run_count += 1
        else:
            flush_consecutive(i)
            run_key, run_start, run_count, run_step = key, i, 1, step
    flush_consecutive(len(steps))

    # Scattered repeats inside a sliding window. A later start whose hits
    # are a subset of an already-emitted span for the same key is skipped,
    # so one scattered pattern yields one finding, not one per occurrence.
    emitted_spans: Dict[str, List[Tuple[int, int]]] = {}
    step_by_index = {s.index: s for s in steps}
    for f in findings:
        first = step_by_index.get(f.start_step)
        if first is not None:
            emitted_spans.setdefault(args_key(first), []).append(
                (f.start_step, f.end_step)
            )
    for i, step in enumerate(steps):
        key = args_key(step)
        span = steps[i : i + window]
        hits = [s for s in span if args_key(s) == key]
        if len(hits) < min_repeat:
            continue
        # Only if the hits are not purely the consecutive case already
        # reported (consecutive hits are a contiguous block starting at i).
        if all(hits[j + 1].index == hits[j].index + 1 for j in range(len(hits) - 1)):
            continue
        if any(
            s <= hits[0].index and hits[-1].index <= e
            for s, e in emitted_spans.get(key, [])
        ):
            continue
        findings.append(
            Finding(
                kind="exact_repeat",
                start_step=hits[0].index,
                end_step=hits[-1].index,
                detail=(
                    f"Call repeated {len(hits)} times within {window} steps: "
                    f"{step.tool} {normalize_text(step.args)}"
                ),
                severity="stall",
                extra={
                    "tool": step.tool,
                    "count": len(hits),
                    "consecutive": False,
                },
            )
        )
        # Remember this span so later starts whose hits are a subset of it
        # are suppressed by the check above.
        emitted_spans.setdefault(key, []).append(
            (hits[0].index, hits[-1].index)
        )
    return findings


def detect_cycles(
    steps: List[Step], min_cycles: int = 2, max_period: int = 8
) -> List[Finding]:
    """A repeating sequence of tool names of period 2..8.

    A cycle counts only when the full period repeats at least
    ``min_cycles`` times (default 2 full cycles, i.e. the pattern appears
    twice back-to-back). Single tool repeats are exact repeats, not cycles,
    so period 1 is excluded.
    """
    findings: List[Finding] = []
    names = [tool_name(s) for s in steps]
    n = len(names)
    if n < 4:
        return findings
    reported_spans = []
    for i in range(n):
        for period in range(2, max_period + 1):
            if i + period * min_cycles > n:
                break
            pattern = names[i : i + period]
            if len(set(pattern)) == 1:
                # A constant run is an exact repeat, not a cycle.
                continue
            if names[i + period : i + 2 * period] != pattern:
                continue
            cycles = 2
            while (
                i + (cycles + 1) * period <= n
                and names[i + cycles * period : i + (cycles + 1) * period] == pattern
            ):
                cycles += 1
            start, end = i, i + cycles * period - 1
            if any(s <= start and end <= e for s, e in reported_spans):
                continue
            reported_spans.append((start, end))
            findings.append(
                Finding(
                    kind="cycle",
                    start_step=steps[start].index,
                    end_step=steps[end].index,
                    detail=(
                        f"Tool cycle repeated {cycles} times "
                        f"(period {period}): {' -> '.join(pattern)}"
                    ),
                    severity="stall",
                    extra={
                        "period": period,
                        "cycles": cycles,
                        "pattern": pattern,
                    },
                )
            )
    return findings


def detect_reread_without_write(steps: List[Step]) -> List[Finding]:
    """Same file Read/Get twice with no intervening write for that path.

    A write, edit, create, or mutating bash command mentioning the path
    resets the tracker, so a re-read *after* a write is not a finding.
    """
    findings: List[Finding] = []
    last_read: Dict[str, Step] = {}
    for step in steps:
        path = path_from_args(step.args)
        if is_write(step):
            if path is not None:
                last_read.pop(path, None)
            else:
                # A mutating bash command may mention paths in its text.
                for known in list(last_read):
                    if bash_mentions_path(step, known):
                        last_read.pop(known, None)
            continue
        if is_read(step) and path is not None:
            if path in last_read:
                prev = last_read[path]
                findings.append(
                    Finding(
                        kind="reread_without_write",
                        start_step=prev.index,
                        end_step=step.index,
                        detail=(
                            f"File re-read with no intervening write: "
                            f"{step.tool} {path}"
                        ),
                        severity="degraded",
                        extra={"tool": step.tool, "path": path},
                    )
                )
            last_read[path] = step
    return findings


def detect_error_cycles(steps: List[Step], min_repeat: int = 3) -> List[Finding]:
    """The same normalised error text recurring >= ``min_repeat`` times."""
    findings: List[Finding] = []
    by_error: Dict[str, List[Step]] = {}
    original: Dict[str, str] = {}
    for step in steps:
        if not step.has_error:
            continue
        key = normalize_error(step.error)
        if not key:
            continue
        by_error.setdefault(key, []).append(step)
        original.setdefault(key, normalize_text(step.error))
    for key, hits in by_error.items():
        if len(hits) >= min_repeat:
            findings.append(
                Finding(
                    kind="error_cycle",
                    start_step=hits[0].index,
                    end_step=hits[-1].index,
                    detail=(
                        f"Same error {len(hits)} times: {original[key]}"
                    ),
                    severity="degraded",
                    extra={"count": len(hits), "error": original[key]},
                )
            )
    return findings


def _is_legitimate_retry(run: List[Step]) -> bool:
    """Distinguish a retry loop from a dead loop inside a stagnation run.

    Legitimate retry: errors are present and the agent is *varying* its
    arguments (different commands, different parameters) rather than
    replaying the identical call. A run with no errors, or with the same
    call replayed, is not a retry -- it is dead.
    """
    if not any(s.has_error for s in run):
        return False
    distinct = {args_key(s) for s in run}
    if len(distinct) < 2:
        return False
    # If one call dominates the run (repeated >= 3 times), it is a dead
    # loop wearing a retry's clothes, not genuine variation.
    counts: Dict[str, int] = {}
    for s in run:
        counts[args_key(s)] = counts.get(args_key(s), 0) + 1
    return max(counts.values()) < 3


def detect_stagnation(steps: List[Step], window: int = 8) -> List[Finding]:
    """``window`` consecutive steps with no state-changing call.

    Maximal runs are reported once each. A run that looks like a
    legitimate retry loop (errors + varying args) is ``degraded``;
    every other stagnation run is a ``stall``.
    """
    findings: List[Finding] = []
    if window < 2 or not steps:
        return findings
    i = 0
    n = len(steps)
    while i < n:
        if is_state_changing(steps[i]):
            i += 1
            continue
        j = i
        while j < n and not is_state_changing(steps[j]):
            j += 1
        run = steps[i:j]
        if len(run) >= window:
            legitimate = _is_legitimate_retry(run)
            findings.append(
                Finding(
                    kind="stagnation_window",
                    start_step=run[0].index,
                    end_step=run[-1].index,
                    detail=(
                        f"{len(run)} consecutive steps with no state-changing "
                        f"call"
                        + (
                            " (looks like a legitimate retry loop: errors "
                            "with varying arguments)"
                            if legitimate
                            else ""
                        )
                    ),
                    severity="degraded" if legitimate else "stall",
                    extra={
                        "length": len(run),
                        "legitimate_retry": legitimate,
                    },
                )
            )
        i = j
    return findings


def compute_progress(steps: List[Step]) -> Tuple[float, int]:
    """Progress score: fraction of steps that change state or add new info.

    A step counts as progress if it is state-changing, or if it produces
    a non-empty, not-previously-seen output. Errors never count, and an
    output identical to one already seen adds nothing. Returns
    ``(score, progress_step_count)``; an empty trace scores 0.0.
    """
    if not steps:
        return 0.0, 0
    seen_outputs = set()
    progress = 0
    for step in steps:
        if is_state_changing(step):
            progress += 1
            continue
        if step.has_error:
            continue
        text = normalize_text(step.output)
        if text and text not in seen_outputs:
            seen_outputs.add(text)
            progress += 1
    return progress / len(steps), progress


def all_findings(
    steps: List[Step], min_repeat: int = 3, window: int = 8
) -> List[Finding]:
    """Run every detector and return findings in trace order."""
    found = (
        detect_exact_repeat(steps, min_repeat=min_repeat)
        + detect_cycles(steps)
        + detect_reread_without_write(steps)
        + detect_error_cycles(steps, min_repeat=min_repeat)
        + detect_stagnation(steps, window=window)
    )
    return sorted(found, key=lambda f: (f.start_step, f.end_step, f.kind))
