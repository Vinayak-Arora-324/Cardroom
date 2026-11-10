import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["AITABLE_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.json")

from table import agents  # noqa: E402


class GrokBackendTest(unittest.TestCase):
    def run_with(self, stdout, returncode=0, seat=None):
        seen = {}

        def fake_run(cmd, stdin_text, cwd, timeout, env):
            seen["cmd"], seen["env"] = cmd, env
            with open(cmd[cmd.index("--prompt-file") + 1]) as f:
                seen["prompt"] = f.read()
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")

        orig = agents._run
        agents._run = fake_run
        try:
            result = agents.call_grok(seat or {"model": "grok-4.7", "effort": "low"}, "SYSTEM", "PROMPT", {"type": "object"})
        finally:
            agents._run = orig
        return result, seen

    def test_command_locks_down_tools_and_passes_seat(self):
        os.environ["XAI_API_KEY"] = "xai-test"
        try:
            _, seen = self.run_with(json.dumps({"text": "", "structuredOutput": {"ok": True}}))
        finally:
            del os.environ["XAI_API_KEY"]
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "todo_write")
        self.assertIn("--no-subagents", cmd)
        self.assertIn("--disable-web-search", cmd)
        self.assertEqual(cmd[cmd.index("--system-prompt-override") + 1], "SYSTEM")
        self.assertEqual(cmd[cmd.index("-m") + 1], "grok-4.7")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "low")
        self.assertIn("--json-schema", cmd)
        self.assertEqual(seen["prompt"], "PROMPT")
        self.assertNotIn("XAI_API_KEY", seen["env"])

    def test_structured_output_and_cost(self):
        (reply, meta), _ = self.run_with(json.dumps({"text": "ignored", "structuredOutput": {"action": "call"}, "total_cost_usd": 0.01}))
        self.assertEqual(reply, {"action": "call"})
        self.assertEqual(meta["cost_usd"], 0.01)

    def test_falls_back_to_text(self):
        (reply, _), _ = self.run_with(json.dumps({"text": 'Sure: {"action": "fold"}'}))
        self.assertEqual(reply, {"action": "fold"})

    def test_error_object(self):
        with self.assertRaisesRegex(agents.AgentError, "unknown model"):
            self.run_with(json.dumps({"type": "error", "message": "unknown model id"}), returncode=1)
        with self.assertRaisesRegex(agents.AgentError, "grok login"):
            self.run_with(json.dumps({"type": "error", "message": "Please sign in"}), returncode=1)

    def test_registered(self):
        self.assertIn("grok", agents.BACKENDS)
        self.assertIn("grok", agents.CALLERS)
        self.assertIn("grok", agents.model_catalog())


if __name__ == "__main__":
    unittest.main()
