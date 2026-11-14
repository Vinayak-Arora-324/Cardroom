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


class CodexBackendTest(unittest.TestCase):
    def run_with(self, reply, returncode=0, seat=None, stderr=""):
        seen = {}

        def fake_run(cmd, stdin_text, cwd, timeout, env):
            seen["cmd"], seen["env"], seen["stdin"] = cmd, env, stdin_text
            if reply is not None:
                with open(cmd[cmd.index("-o") + 1], "w") as f:
                    f.write(reply)
            return subprocess.CompletedProcess(cmd, returncode, "", stderr)

        orig = agents._run
        agents._run = fake_run
        try:
            result = agents.call_codex(seat or {"model": "gpt-6-astra", "effort": "low"},
                                       "SYSTEM", "PROMPT", {"type": "object"})
        finally:
            agents._run = orig
        return result, seen

    def test_command_disables_all_tools(self):
        _, seen = self.run_with('{"ok": true}')
        cmd = seen["cmd"]
        self.assertEqual(cmd[:2], ["codex", "exec"])
        self.assertIn("--ignore-user-config", cmd)
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
        disabled = {cmd[i + 1] for i, a in enumerate(cmd) if a == "--disable"}
        self.assertEqual(disabled, {"shell_tool", "unified_exec"})
        self.assertIn('web_search="disabled"', cmd)
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-6-astra")
        self.assertEqual(cmd[cmd.index("-c", cmd.index("-m")) + 1], 'model_reasoning_effort="low"')
        self.assertEqual(cmd[-1], "-")
        self.assertIn("PROMPT", seen["stdin"])

    def test_reply_parsed(self):
        (reply, meta), _ = self.run_with('Sure: {"action": "call"}')
        self.assertEqual(reply, {"action": "call"})
        self.assertEqual(meta, {})

    def test_login_error(self):
        with self.assertRaisesRegex(agents.AgentError, "codex login"):
            self.run_with(None, returncode=1, stderr="401 Unauthorized")


if __name__ == "__main__":
    unittest.main()
