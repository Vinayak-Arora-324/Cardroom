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


def events(*items):
    return "\n".join(json.dumps(e) for e in items) + "\n"


def text(msg, part, body):
    return {"type": "text", "part": {"id": part, "messageID": msg, "type": "text", "text": body}}


def finish(cost):
    return {"type": "step_finish", "part": {"type": "step-finish", "cost": cost}}


class OpencodeBackendTest(unittest.TestCase):
    def run_with(self, stdout, returncode=0, seat=None, stderr=""):
        seen = {}

        def fake_run(cmd, stdin_text, cwd, timeout, env):
            seen["cmd"], seen["env"], seen["stdin"] = cmd, env, stdin_text
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

        orig = agents._run
        agents._run = fake_run
        try:
            result = agents.call_opencode(seat or {"model": "openrouter/openrouter/free", "effort": "low"},
                                          "SYSTEM", "PROMPT", {"type": "object"})
        finally:
            agents._run = orig
        return result, seen

    def test_command_uses_private_toolless_agent(self):
        _, seen = self.run_with(events(text("m1", "p1", '{"ok": true}'), finish(0)))
        cmd = seen["cmd"]
        self.assertEqual(cmd[:2], ["opencode", "run"])
        self.assertIn("--standalone", cmd)
        self.assertEqual(cmd[cmd.index("--format") + 1], "json")
        self.assertEqual(cmd[cmd.index("-m") + 1], "openrouter/openrouter/free#low")
        agent_name = cmd[cmd.index("--agent") + 1]
        agent = json.loads(seen["env"]["OPENCODE_CONFIG_CONTENT"])["agent"][agent_name]
        self.assertEqual(agent["prompt"], "SYSTEM")
        self.assertEqual(agent["tools"], {"*": False})
        self.assertEqual(agent["permission"], {"*": "deny"})
        self.assertTrue(seen["stdin"].startswith("PROMPT"))

    def test_default_model_passes_no_flag(self):
        _, seen = self.run_with(events(text("m1", "p1", "{}")), seat={"model": "", "effort": "high"})
        self.assertNotIn("-m", seen["cmd"])

    def test_reply_and_cost(self):
        out = events(
            text("m1", "p1", "thinking about it"), finish(0.001),
            text("m2", "p2", 'Sure: {"action": '), text("m2", "p3", '"call"}'), finish(0.002),
        )
        (reply, meta), _ = self.run_with(out)
        self.assertEqual(reply, {"action": "call"})
        self.assertAlmostEqual(meta["cost_usd"], 0.003)

    def test_errors(self):
        err = {"type": "error", "error": {"type": "provider.no-route", "message": "Model unavailable: x/y"}}
        with self.assertRaisesRegex(agents.AgentError, "Model unavailable"):
            self.run_with(events(err), returncode=1)
        err = {"type": "error", "error": {"message": "401 Unauthorized: missing api key"}}
        with self.assertRaisesRegex(agents.AgentError, "opencode auth login"):
            self.run_with(events(err), returncode=1)

    def test_free_flag(self):
        free = {"id": "x/y:free", "providerID": "openrouter", "cost": [{"input": 0, "output": 0}]}
        router = {"id": "openrouter/auto", "providerID": "openrouter", "cost": [{"input": 0, "output": 0}]}
        paid = {"id": "a/b-free-ish", "providerID": "openrouter", "cost": [{"input": 1, "output": 2}]}
        self.assertTrue(agents._opencode_is_free(free))
        self.assertFalse(agents._opencode_is_free(router))
        self.assertFalse(agents._opencode_is_free(paid))

    def test_registered_and_openrouter_gone(self):
        self.assertIn("opencode", agents.BACKENDS)
        self.assertIn("opencode", agents.CALLERS)
        self.assertNotIn("openrouter", agents.BACKENDS)
        self.assertNotIn("openrouter", agents.CALLERS)


class RunTimeoutTest(unittest.TestCase):
    def test_timeout_kills_process_group(self):
        with self.assertRaisesRegex(agents.AgentError, "timed out"):
            agents._run(["sh", "-c", "sleep 30 & sleep 30"], "", None, 0.5, dict(os.environ))


if __name__ == "__main__":
    unittest.main()
