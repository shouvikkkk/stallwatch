import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from stallwatch.cli import main
from stallwatch.models import Step
from stallwatch.report import analysis_to_dict, analyze, render_text

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def mk(index, tool, args=None, output="", error=None, tokens=None):
    return Step(index=index, tool=tool, args=args or {}, output=output,
                error=error, tokens=tokens)


def run_cli(argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class TestReport(unittest.TestCase):
    def test_healthy_verdict(self):
        steps = [
            mk(1, "Read", {"file_path": "a.py"}, output="v1"),
            mk(2, "Edit", {"file_path": "a.py"}, output="edited"),
            mk(3, "Bash", {"command": "npm test"}, output="pass"),
        ]
        analysis = analyze(steps)
        self.assertEqual(analysis.verdict, "HEALTHY")
        self.assertEqual(analysis.exit_code, 0)

    def test_stalled_verdict_and_wasted_tokens(self):
        steps = [
            mk(i, "Read", {"file_path": "a.py"}, output="same", tokens=100)
            for i in (1, 2, 3)
        ]
        analysis = analyze(steps)
        self.assertEqual(analysis.verdict, "STALLED")
        self.assertEqual(analysis.exit_code, 2)
        self.assertEqual(analysis.first_stall_step, 1)
        self.assertEqual(analysis.wasted_tokens, 300)

    def test_wasted_tokens_unknown_when_no_usage_data(self):
        steps = [mk(i, "Read", {"file_path": "a.py"}, output="same") for i in (1, 2, 3)]
        analysis = analyze(steps)
        self.assertIsNone(analysis.wasted_tokens)
        self.assertTrue(analysis.wasted_tokens_unknown)

    def test_wasted_tokens_zero_when_no_stall(self):
        steps = [mk(1, "Read", {"file_path": "a.py"}, output="v1", tokens=50)]
        analysis = analyze(steps)
        self.assertEqual(analysis.wasted_tokens, 0)

    def test_degraded_verdict_for_error_cycle(self):
        # Tool sequence deliberately avoids any repeating period, so the
        # only finding is the error cycle itself.
        steps = [
            mk(1, "Read", {"file_path": "a.py"}, output="v1"),
            mk(2, "Write", {"file_path": "b.py"}, output="ok"),
            mk(3, "Bash", {"command": "try-a"}, error="boom"),
            mk(4, "Glob", {"pattern": "*.py"}, output="a.py b.py"),
            mk(5, "Bash", {"command": "try-b"}, error="boom"),
            mk(6, "Read", {"file_path": "c.py"}, output="v2"),
            mk(7, "Bash", {"command": "try-c"}, error="boom"),
        ]
        analysis = analyze(steps)
        self.assertEqual(analysis.verdict, "DEGRADED")
        self.assertEqual(analysis.exit_code, 1)

    def test_unknown_verdict_for_empty_trace(self):
        analysis = analyze([])
        self.assertEqual(analysis.verdict, "UNKNOWN")
        self.assertEqual(analysis.exit_code, 3)

    def test_json_dict_carries_same_fields(self):
        steps = [mk(i, "Read", {"file_path": "a.py"}, output="same") for i in (1, 2, 3)]
        data = analysis_to_dict(analyze(steps))
        self.assertEqual(data["verdict"], "STALLED")
        self.assertEqual(data["first_stall_step"], 1)
        self.assertIsNone(data["wasted_tokens"])
        self.assertTrue(data["findings"])

    def test_text_report_names_stall_step_and_wasted_tokens(self):
        steps = [mk(i, "Read", {"file_path": "a.py"}, output="same", tokens=10)
            for i in (1, 2, 3)]
        text = render_text(analyze(steps))
        self.assertIn("First stall step: 1", text)
        self.assertIn("Wasted tokens: 30", text)


class TestCli(unittest.TestCase):
    def test_looping_example_is_stalled(self):
        code, out = run_cli(["check", str(EXAMPLES / "looping-trace.jsonl")])
        self.assertEqual(code, 2)
        self.assertIn("STALLED", out)
        self.assertIn("First stall step: 1", out)
        self.assertIn("Wasted tokens: 1500", out)

    def test_healthy_example_is_healthy(self):
        code, out = run_cli(["check", str(EXAMPLES / "healthy-trace.jsonl")])
        self.assertEqual(code, 0)
        self.assertIn("HEALTHY", out)

    def test_json_format_is_machine_readable(self):
        code, out = run_cli([
            "check", str(EXAMPLES / "looping-trace.jsonl"), "--format", "json",
        ])
        self.assertEqual(code, 2)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "STALLED")
        self.assertEqual(data["wasted_tokens"], 1500)
        self.assertEqual(data["total_steps"], 10)

    def test_missing_file_is_unknown(self):
        code, _out = run_cli(["check", "/nonexistent/trace.jsonl"])
        self.assertEqual(code, 3)

    def test_empty_file_is_unknown(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            path = f.name
        code, out = run_cli(["check", path])
        self.assertEqual(code, 3)
        self.assertIn("UNKNOWN", out)

    def test_malformed_file_is_unknown(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("not json\nmore garbage\n")
            path = f.name
        code, out = run_cli(["check", path])
        self.assertEqual(code, 3)
        self.assertIn("UNKNOWN", out)

    def test_min_repeat_flag_relaxes_loop_detection(self):
        # With min-repeat 5, the looping example's scattered repeats (5 of
        # each call) still hit the threshold, but a 3-repeat trace does not.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for i in range(1, 4):
                f.write(json.dumps({
                    "tool": "Read", "args": {"file_path": "a.py"},
                    "output": "same", "tokens": 10}) + "\n")
            path = f.name
        code, _out = run_cli(["check", path, "--min-repeat", "5"])
        # 3 repeats < 5, and only 3 steps < stagnation window 8, so no stall;
        # progress is low (duplicate output), so the verdict is DEGRADED.
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
