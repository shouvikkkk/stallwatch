import unittest

from stallwatch.models import Step
from stallwatch.normalize import (
    args_key,
    is_mutating_bash,
    is_state_changing,
    is_write,
    normalize_args,
    normalize_error,
    normalize_text,
    path_from_args,
)


def step(tool, args=None, **kw):
    return Step(index=1, tool=tool, args=args or {}, **kw)


class TestNormalize(unittest.TestCase):
    def test_normalize_text_collapses_whitespace(self):
        self.assertEqual(normalize_text("  a   b\n c "), "a b c")

    def test_normalize_args_sorts_keys_and_trims_strings(self):
        a = normalize_args({"b": "  x  ", "a": 1})
        b = normalize_args({"a": 1, "b": "x"})
        self.assertEqual(a, b)

    def test_args_key_ignores_key_order_and_whitespace(self):
        s1 = step("Read", {"file_path": "src/app.py", "n": 1})
        s2 = step("Read", {"n": 1, "file_path": "  src/app.py "})
        self.assertEqual(args_key(s1), args_key(s2))

    def test_args_key_distinguishes_different_args(self):
        s1 = step("Read", {"file_path": "a.py"})
        s2 = step("Read", {"file_path": "b.py"})
        self.assertNotEqual(args_key(s1), args_key(s2))

    def test_args_key_distinguishes_tools(self):
        s1 = step("Read", {"file_path": "a.py"})
        s2 = step("Write", {"file_path": "a.py"})
        self.assertNotEqual(args_key(s1), args_key(s2))

    def test_normalize_error_groups_digit_variants(self):
        self.assertEqual(
            normalize_error("Failed after 3 retries."),
            normalize_error("failed after 4 retries"),
        )

    def test_normalize_error_keeps_different_messages_apart(self):
        self.assertNotEqual(
            normalize_error("file not found"), normalize_error("timeout")
        )

    def test_is_state_changing_write_edit(self):
        self.assertTrue(is_state_changing(step("Write")))
        self.assertTrue(is_state_changing(step("Edit")))
        self.assertFalse(is_state_changing(step("Read")))

    def test_mutating_bash_redirection(self):
        self.assertTrue(
            is_mutating_bash(step("Bash", {"command": "echo hi > out.txt"}))
        )
        self.assertTrue(is_state_changing(step("Bash", {"command": "rm old.txt"})))

    def test_non_mutating_bash_not_state_changing(self):
        self.assertFalse(
            is_mutating_bash(step("Bash", {"command": "ls -la 2>&1"}))
        )
        self.assertFalse(is_state_changing(step("Bash", {"command": "npm test"})))

    def test_git_commit_is_mutating(self):
        self.assertTrue(
            is_state_changing(step("Bash", {"command": "git commit -m x"}))
        )

    def test_is_write(self):
        self.assertTrue(is_write(step("Edit")))
        self.assertFalse(is_write(step("Read")))

    def test_path_from_args(self):
        self.assertEqual(path_from_args({"file_path": "a.py"}), "a.py")
        self.assertEqual(path_from_args({"path": "b.py"}), "b.py")
        self.assertIsNone(path_from_args({"command": "ls"}))


if __name__ == "__main__":
    unittest.main()
