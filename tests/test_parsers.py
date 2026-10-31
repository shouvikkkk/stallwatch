import json
import unittest

from stallwatch.parsers import parse_trace_text


class TestParsers(unittest.TestCase):
    def test_generic_format(self):
        text = json.dumps(
            {"step": 1, "tool": "Read", "args": {"file_path": "a.py"},
             "output": "hello", "tokens": 42, "unknown_field": "ignored"}
        )
        result = parse_trace_text(text)
        self.assertEqual(len(result.steps), 1)
        step = result.steps[0]
        self.assertEqual(step.tool, "Read")
        self.assertEqual(step.args["file_path"], "a.py")
        self.assertEqual(step.output, "hello")
        self.assertEqual(step.tokens, 42)
        self.assertEqual(step.index, 1)

    def test_generic_step_without_tokens(self):
        text = json.dumps({"tool": "Bash", "args": {"command": "ls"}, "output": "x"})
        result = parse_trace_text(text)
        self.assertIsNone(result.steps[0].tokens)

    def test_claude_code_tool_use_and_result(self):
        lines = [
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "toolu_1",
                                 "name": "Read", "input": {"file_path": "a.py"}}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            }),
            json.dumps({
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                 "content": "hello"}],
                },
            }),
        ]
        result = parse_trace_text("\n".join(lines))
        self.assertEqual(len(result.steps), 1)
        step = result.steps[0]
        self.assertEqual(step.tool, "Read")
        self.assertEqual(step.output, "hello")
        self.assertEqual(step.tokens, 15)

    def test_claude_error_result_attaches_as_error(self):
        lines = [
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "npm test"}}]}}),
            json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "FAIL", "is_error": True}]}}),
        ]
        result = parse_trace_text("\n".join(lines))
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].error, "FAIL")

    def test_codex_function_call_and_output(self):
        lines = [
            json.dumps({"type": "response_item", "item": {
                "type": "function_call", "name": "Bash",
                "arguments": '{"command": "ls"}', "call_id": "call_1"}}),
            json.dumps({"type": "response_item", "item": {
                "type": "function_call_output", "call_id": "call_1",
                "output": "file.txt"}}),
        ]
        result = parse_trace_text("\n".join(lines))
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].tool, "Bash")
        self.assertEqual(result.steps[0].args["command"], "ls")
        self.assertEqual(result.steps[0].output, "file.txt")

    def test_malformed_lines_skipped_but_valid_lines_kept(self):
        text = "\n".join([
            "not json at all",
            json.dumps({"tool": "Read", "args": {}, "output": "x"}),
            "{bad json",
        ])
        result = parse_trace_text(text)
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.skipped_lines, 2)

    def test_empty_text_has_no_steps(self):
        result = parse_trace_text("")
        self.assertEqual(result.steps, [])

    def test_all_malformed_has_no_steps(self):
        result = parse_trace_text("oops\nstill not json")
        self.assertEqual(result.steps, [])
        self.assertEqual(result.skipped_lines, 2)

    def test_json_array_line_expands_to_steps(self):
        text = json.dumps([
            {"tool": "Read", "args": {}, "output": "a"},
            {"tool": "Write", "args": {}, "output": "b"},
        ])
        result = parse_trace_text(text)
        self.assertEqual(len(result.steps), 2)
        self.assertEqual([s.index for s in result.steps], [1, 2])

    def test_text_only_assistant_message_is_not_a_step(self):
        text = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Let me check that."}]}})
        result = parse_trace_text(text)
        self.assertEqual(result.steps, [])

    def test_string_arguments_parsed_as_json(self):
        text = json.dumps({"tool": "Bash", "args": '{"command": "ls"}'})
        # Generic parser reads "args" key, not "arguments" string here;
        # the string-args path is exercised via the arguments key below.
        result = parse_trace_text(text)
        self.assertEqual(result.steps[0].args["command"], "ls")


if __name__ == "__main__":
    unittest.main()
