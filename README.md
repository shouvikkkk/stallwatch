# stallwatch

Coding agents silently burn money spinning in tool-call loops with no
progress — and today's guards only count steps. None of them detect
**stagnation**.

stallwatch is a stdlib-only Python CLI that reads a *finished* agent run
trace and reports whether the agent stalled, where it stalled, and how
much it wasted. It does trace forensics after the run, not live
supervision during it.

## The problem

A coding agent can keep calling tools, reading the same file, and
re-running the same failing command for dozens of steps while making no
progress at all. Step caps do not catch this: a loop of 40 steps is well
under a 200-step cap, and every step "succeeds" in the narrow sense that
a tool returned something. The bill, meanwhile, keeps growing with every
token the loop consumes.

### Evidence

Verified 31 October 2026:

- **Microsoft Research / Georgia Tech, "Babysitting AI Agents"**
  (arXiv:2610.34748, submitted 26 October 2026) built a taxonomy of
  agent failures from 163,487 Reddit posts. Its largest failure category
  is "unbounded or idle execution" — an agent running "without making
  meaningful progress" — accounting for 1,017 of 6,623 failure instances.
  The paper notes this category has one of the least mature research
  communities around it. That is the gap stallwatch sits in: detecting
  the stall itself, from the trace, rather than counting steps.
- **dev.to, "What happens when an AI agent gets stuck in a loop":**
  > "An agent can get stuck repeating the same action without making
  > meaningful progress."
- **Cost anecdote — flagged as second-hand:** a Reddit user "spent about
  $500 in two days trying to debug their app, without making meaningful
  progress" (as summarised in a Softr blog post; the original post was
  not independently verified for this tool). It is included here only as
  an illustration of the cost shape, not as a verified measurement.

## Setup

Python 3.9+ is the only requirement. There are no third-party
dependencies.

```bash
# From the repository root, run directly:
python3 -m stallwatch check examples/looping-trace.jsonl

# Or install it (editable install for development):
pip install -e .
stallwatch check examples/looping-trace.jsonl
```

## Usage

```bash
stallwatch check <trace-file> [--format text|json] [--min-repeat N] [--window N]
```

- `--format`: `text` (default) or `json`. The JSON report carries the
  same fields machine-readably: verdict, exit code, total steps, progress
  score, first stall step, wasted tokens, and every finding.
- `--min-repeat`: repeat count that counts as a loop (default 3).
- `--window`: stagnation window — consecutive non-state-changing steps
  that count as a stall (default 8).

### Verdicts and exit codes

| Verdict | Exit | Meaning |
| --- | --- | --- |
| HEALTHY | 0 | No stall or warning pattern found. |
| DEGRADED | 1 | Warning pattern: repeated errors, a re-read, a legitimate retry loop, or low progress — but no hard loop. |
| STALLED | 2 | A hard loop or a dead stagnation window was found. |
| UNKNOWN | 3 | No parseable steps in the trace (fail closed — never reported as healthy). A missing or unreadable file also exits 3. |

### Example

The repository ships two traces in `examples/`. The looping trace is an
agent reading `src/app.py` and re-running `npm test` ten times, always
with the same failing result:

```bash
$ python3 -m stallwatch check examples/looping-trace.jsonl
Verdict: STALLED (exit 2)
Steps: 10  Progress: 1/10 (10%)
First stall step: 1
Wasted tokens: 1500
- [DEGRADED] reread_without_write (steps 1-3): File re-read with no intervening write: Read src/app.py
- [STALL] exact_repeat (steps 1-9): Call repeated 5 times within 10 steps: Read {'file_path': 'src/app.py'}
- [STALL] cycle (steps 1-10): Tool cycle repeated 5 times (period 2): read -> bash
- [STALL] stagnation_window (steps 1-10): 10 consecutive steps with no state-changing call
- [DEGRADED] error_cycle (steps 2-10): Same error 5 times: FAIL: 1 test failed in test_app.py
- [STALL] exact_repeat (steps 2-10): Call repeated 5 times within 10 steps: Bash {'command': 'npm test'}
- [DEGRADED] reread_without_write (steps 3-5): File re-read with no intervening write: Read src/app.py
- [DEGRADED] reread_without_write (steps 5-7): File re-read with no intervening write: Read src/app.py
- [DEGRADED] reread_without_write (steps 7-9): File re-read with no intervening write: Read src/app.py
```

The healthy trace edits, tests, writes, commits, and only re-reads a
file *after* editing it:

```bash
$ python3 -m stallwatch check examples/healthy-trace.jsonl
Verdict: HEALTHY (exit 0)
Steps: 7  Progress: 7/7 (100%)
Wasted tokens: 0
No findings.
```

JSON output for the same looping trace (abbreviated — the real output lists all 9 findings in full):

```bash
$ python3 -m stallwatch check examples/looping-trace.jsonl --format json
{
  "verdict": "STALLED",
  "exit_code": 2,
  "total_steps": 10,
  "progress_score": 0.1,
  "first_stall_step": 1,
  "wasted_tokens": 1500,
  "findings": [ "..." ]
}
```

## Trace formats

stallwatch reads JSONL (one JSON object per line). Unknown fields are
ignored. Lines that are not valid JSON, or that carry no recognisable
tool call, are skipped and counted; if *no* step can be recovered at
all, the verdict is UNKNOWN.

### Generic JSONL (the fallback format)

One step per line:

```json
{"step": 1, "tool": "Read", "args": {"file_path": "src/app.py"}, "output": "def main():\n    pass\n", "tokens": 100}
{"step": 2, "tool": "Bash", "args": {"command": "npm test"}, "error": "FAIL: 1 test failed in test_app.py", "tokens": 200}
```

Fields: `step` (optional; order is file order), `tool`, `args` (object,
or a JSON string), `output`, `error`, `tokens`. `result` is accepted as
an alias for `output`, and `input` / `arguments` / `parameters` as
aliases for `args`.

### Claude Code JSONL

Assistant messages carrying `tool_use` blocks and the matching user
messages carrying `tool_result` blocks are combined into single steps;
`tool_result` records never become steps of their own. Usage blocks
(`input_tokens` / `output_tokens`) are summed into the step's token
count when present.

### Codex-style rollout JSONL

Function-call records (`function_call` items, `tool_calls` lists, flat
`function` records) are read on a best-effort basis, and
`function_call_output` records are attached to their call by call id
when available, otherwise to the latest step still awaiting a result.

## Detectors

Each detector is a real, unit-tested function in `stallwatch/detectors.py`:

1. **Exact repeat** — the same tool with normalised arguments (sorted
   keys, whitespace-normalised strings) repeated at least `--min-repeat`
   times, consecutively *or* scattered inside a 10-step sliding window.
2. **Cycle detection** — a repeating sequence of tool names of period
   2–8 completing at least 2 full cycles (a constant run is an exact
   repeat, not a cycle, and is excluded here).
3. **Re-read without write** — the same file read twice with no
   intervening write, edit, create, or mutating bash command for that
   path. A re-read *after* a write is not a finding.
4. **Error cycle** — the same normalised error text (lower-cased,
   whitespace-collapsed, digit runs grouped) recurring at least 3 times.
5. **Stagnation window** — `--window` (default 8) consecutive steps with
   no state-changing call (writes, edits, creates, deletes, mutating
   bash such as redirections / `rm` / `git commit`, commits, PRs).
   This detector distinguishes a **legitimate retry loop** — errors
   present and arguments varying between attempts — from a **dead
   loop** (no errors, or the identical call replayed). A legitimate
   retry is reported as DEGRADED, not STALLED.
6. **Progress score and wasted tokens** — the progress score is the
   fraction of steps that are state-changing or produce new information
   (a non-empty output not seen before; errors never count). The
   wasted-token estimate is the sum of usage tokens from the first
   stall step onward. If no step from that point carries usage data, the
   report says "unknown" — it is never estimated from step counts.

## Differentiation

- **LangGraph recursion limits / step caps** count steps. A loop that
  stays under the cap passes them silently; stallwatch instead asks
  whether the steps *went anywhere*, so a 40-step loop under a 200-step
  cap is still flagged.
- **Provider spend alerts** fire after the fact, on aggregate spend, and
  say nothing about *which* steps were wasted or *why* the agent was
  stuck. stallwatch is trace forensics: it names the stall step, the
  repeated call or cycle, and the token count attributable to the stall.
- **Log viewers and trace UIs** show you the trace; stallwatch judges
  it, with verdicts and exit codes you can gate CI or a post-run report
  on.

## Development and tests

```bash
python3 -m unittest discover -s tests
```

The suite covers every detector plus edge cases: empty traces, single
steps, malformed lines, missing usage data, legitimate retry versus
dead loop, a period-3 cycle, and re-read after write (not a finding).
At release, all 66 tests pass.

## Honest limitations

- **Tested on synthetic traces only.** The parsers for Claude Code and
  Codex formats are best-effort reconstructions of their public shapes
  and have **not** been verified against real Claude Code or Codex
  traces. Real traces may use fields or shapes this version does not
  recognise; unrecognised lines are skipped and counted, and a trace
  with no recognisable steps reports UNKNOWN rather than a false
  verdict.
- **Token estimates depend on usage data being present.** Traces
  without usage blocks report wasted tokens as "unknown". Even with
  usage blocks, the estimate attributes every token from the first
  stall step onward to the stall, which is an upper-bound convention,
  not a measured counterfactual.
- **Heuristics, not proof.** The mutating-bash classification, the
  new-information rule for progress, and the legitimate-retry
  distinction are documented heuristics. A genuinely exploratory run
  with many reads can score low on progress, and a loop that varies
  its arguments cosmetically can look like a retry. The detectors are
  tuned to avoid false STALLED verdicts on varied, error-driven work,
  at the cost of occasionally under-flagging a cleverly varying loop.
- **Finished traces only.** stallwatch does not watch a live run, kill
  a looping agent, or set a budget. It tells you after the run what
  went wrong, so the next run's guard can be better.
- **Single-file scope.** The re-read detector tracks paths named in
  arguments; a write performed through an unrecognised tool or an
  indirect path (a script, a renamed file) will not reset it.

## Licence

MIT — see `LICENSE`. Copyright (c) 2026 Shouvik Maji.
