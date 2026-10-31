import unittest

from stallwatch.detectors import (
    compute_progress,
    detect_cycles,
    detect_error_cycles,
    detect_exact_repeat,
    detect_reread_without_write,
    detect_stagnation,
)
from stallwatch.models import Step


def mk(index, tool, args=None, output="", error=None, tokens=None):
    return Step(
        index=index, tool=tool, args=args or {}, output=output,
        error=error, tokens=tokens,
    )


class TestExactRepeat(unittest.TestCase):
    def test_consecutive_three_is_finding(self):
        steps = [mk(i, "Read", {"file_path": "a.py"}, output="x") for i in (1, 2, 3)]
        found = detect_exact_repeat(steps, min_repeat=3)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "stall")
        self.assertEqual((found[0].start_step, found[0].end_step), (1, 3))

    def test_two_repeats_below_threshold_is_not_finding(self):
        steps = [mk(i, "Read", {"file_path": "a.py"}) for i in (1, 2)]
        self.assertEqual(detect_exact_repeat(steps, min_repeat=3), [])

    def test_key_order_and_whitespace_do_not_break_repeat(self):
        steps = [
            mk(1, "Read", {"file_path": "a.py", "n": 1}),
            mk(2, "Read", {"n": 1, "file_path": "  a.py "}),
            mk(3, "Read", {"file_path": "a.py", "n": 1}),
        ]
        self.assertEqual(len(detect_exact_repeat(steps, min_repeat=3)), 1)

    def test_scattered_repeat_within_window(self):
        steps = [
            mk(1, "Read", {"file_path": "a.py"}),
            mk(2, "Read", {"file_path": "b.py"}),
            mk(3, "Read", {"file_path": "a.py"}),
            mk(4, "Read", {"file_path": "a.py"}),
        ]
        found = detect_exact_repeat(steps, min_repeat=3, window=10)
        self.assertTrue(any(f.start_step == 1 for f in found))

    def test_single_step_no_finding(self):
        self.assertEqual(detect_exact_repeat([mk(1, "Read")]), [])

    def test_empty_trace_no_finding(self):
        self.assertEqual(detect_exact_repeat([]), [])


class TestCycles(unittest.TestCase):
    def test_cycle_period_three(self):
        tools = ["Read", "Bash", "Glob"] * 2
        steps = [mk(i + 1, t, {"n": i}) for i, t in enumerate(tools)]
        found = detect_cycles(steps)
        self.assertTrue(
            any(f.extra.get("period") == 3 and f.extra.get("cycles") == 2 for f in found)
        )

    def test_cycle_period_two(self):
        tools = ["Read", "Bash"] * 3
        steps = [mk(i + 1, t, {"n": i}) for i, t in enumerate(tools)]
        found = detect_cycles(steps)
        self.assertTrue(any(f.extra.get("period") == 2 for f in found))

    def test_no_cycle_for_single_repetition(self):
        tools = ["Read", "Bash", "Glob"]
        steps = [mk(i + 1, t) for i, t in enumerate(tools)]
        self.assertEqual(detect_cycles(steps), [])

    def test_period_one_is_not_a_cycle(self):
        steps = [mk(i + 1, "Read", {"n": i}) for i in range(6)]
        # Period 1 is excluded by design; exact-repeat covers this shape.
        self.assertEqual(detect_cycles(steps), [])


class TestReread(unittest.TestCase):
    def test_reread_without_write_is_finding(self):
        steps = [
            mk(1, "Read", {"file_path": "a.py"}, output="x"),
            mk(2, "Bash", {"command": "ls"}, output="y"),
            mk(3, "Read", {"file_path": "a.py"}, output="x"),
        ]
        found = detect_reread_without_write(steps)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].start_step, found[0].end_step), (1, 3))

    def test_reread_after_write_is_not_finding(self):
        steps = [
            mk(1, "Read", {"file_path": "a.py"}, output="v1"),
            mk(2, "Edit", {"file_path": "a.py"}, output="edited"),
            mk(3, "Read", {"file_path": "a.py"}, output="v2"),
        ]
        self.assertEqual(detect_reread_without_write(steps), [])

    def test_reread_after_mutating_bash_mentioning_path_is_not_finding(self):
        steps = [
            mk(1, "Read", {"file_path": "a.py"}, output="v1"),
            mk(2, "Bash", {"command": "echo x > a.py"}, output="ok"),
            mk(3, "Read", {"file_path": "a.py"}, output="v2"),
        ]
        self.assertEqual(detect_reread_without_write(steps), [])

    def test_reading_different_files_is_not_finding(self):
        steps = [mk(1, "Read", {"file_path": "a.py"}), mk(2, "Read", {"file_path": "b.py"})]
        self.assertEqual(detect_reread_without_write(steps), [])


class TestErrorCycles(unittest.TestCase):
    def test_same_error_three_times(self):
        steps = [mk(i, "Bash", {"command": "t"}, error="boom") for i in (1, 2, 3)]
        found = detect_error_cycles(steps, min_repeat=3)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "degraded")

    def test_two_errors_below_threshold(self):
        steps = [mk(i, "Bash", {"command": "t"}, error="boom") for i in (1, 2)]
        self.assertEqual(detect_error_cycles(steps, min_repeat=3), [])

    def test_digit_variants_group_together(self):
        steps = [
            mk(1, "Bash", error="failed after 3 retries"),
            mk(2, "Bash", error="failed after 4 retries"),
            mk(3, "Bash", error="failed after 5 retries"),
        ]
        self.assertEqual(len(detect_error_cycles(steps, min_repeat=3)), 1)

    def test_different_errors_do_not_group(self):
        steps = [
            mk(1, "Bash", error="not found"),
            mk(2, "Bash", error="timeout"),
            mk(3, "Bash", error="denied"),
        ]
        self.assertEqual(detect_error_cycles(steps, min_repeat=3), [])


class TestStagnation(unittest.TestCase):
    def test_eight_non_state_changing_steps_is_stall(self):
        steps = [mk(i, "Read", {"file_path": f"f{i}.py"}, output=f"v{i}") for i in range(1, 9)]
        found = detect_stagnation(steps, window=8)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "stall")

    def test_seven_steps_below_window_is_not_finding(self):
        steps = [mk(i, "Read", {"file_path": f"f{i}.py"}) for i in range(1, 8)]
        self.assertEqual(detect_stagnation(steps, window=8), [])

    def test_write_breaks_stagnation_run(self):
        steps = [mk(i, "Read", {"file_path": f"f{i}.py"}) for i in range(1, 5)]
        steps.append(mk(5, "Write", {"file_path": "x.py"}))
        steps += [mk(i, "Read", {"file_path": f"f{i}.py"}) for i in range(6, 10)]
        self.assertEqual(detect_stagnation(steps, window=8), [])

    def test_legitimate_retry_is_degraded_not_stall(self):
        steps = [
            mk(i, "Bash", {"command": f"try --port {8000 + i}"}, error="refused")
            for i in range(1, 9)
        ]
        found = detect_stagnation(steps, window=8)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "degraded")
        self.assertTrue(found[0].extra["legitimate_retry"])

    def test_dead_loop_with_errors_is_stall_not_retry(self):
        steps = [
            mk(i, "Bash", {"command": "try --port 8001"}, error="refused")
            for i in range(1, 9)
        ]
        found = detect_stagnation(steps, window=8)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "stall")
        self.assertFalse(found[0].extra["legitimate_retry"])


class TestProgress(unittest.TestCase):
    def test_empty_trace_scores_zero(self):
        self.assertEqual(compute_progress([]), (0.0, 0))

    def test_state_change_and_new_output_count(self):
        steps = [
            mk(1, "Read", {"file_path": "a.py"}, output="one"),
            mk(2, "Write", {"file_path": "a.py"}, output="done"),
        ]
        score, count = compute_progress(steps)
        self.assertEqual((score, count), (1.0, 2))

    def test_duplicate_output_does_not_count_twice(self):
        steps = [
            mk(1, "Read", {"file_path": "a.py"}, output="same"),
            mk(2, "Read", {"file_path": "b.py"}, output="same"),
        ]
        score, count = compute_progress(steps)
        self.assertEqual((score, count), (0.5, 1))

    def test_errors_do_not_count_as_progress(self):
        steps = [mk(1, "Bash", {"command": "t"}, error="boom")]
        self.assertEqual(compute_progress(steps), (0.0, 0))


if __name__ == "__main__":
    unittest.main()
