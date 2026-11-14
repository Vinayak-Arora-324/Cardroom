import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from table import tunnel  # noqa: E402

BANNER = "2026-11-13T10:00:00Z INF |  https://abc-def-ghi.trycloudflare.com  |"


class FakeCloudflared:
    """An executable that prints the given lines, then sleeps. Never runs the real cloudflared."""

    def __init__(self, lines, sleep=60):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cloudflared")
        body = f"#!{sys.executable}\nimport time\n"
        body += "".join(f"print({line!r}, flush=True)\n" for line in lines)
        body += f"time.sleep({sleep})\n"
        with open(self.path, "w") as f:
            f.write(body)
        os.chmod(self.path, 0o755)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestParseUrl(unittest.TestCase):
    def test_banner_line(self):
        self.assertEqual(tunnel.parse_url(BANNER), "https://abc-def-ghi.trycloudflare.com")

    def test_unrelated_cloudflare_link_is_not_the_tunnel(self):
        line = "2026-11-13T10:00:00Z INF see https://www.cloudflare.com/website-terms/ for details"
        self.assertIsNone(tunnel.parse_url(line))

    def test_plain_log_line(self):
        self.assertIsNone(tunnel.parse_url("2026-11-13T10:00:01Z INF Starting tunnel"))


class TestStart(unittest.TestCase):
    def setUp(self):
        self.fakes = []

    def tearDown(self):
        for fake in self.fakes:
            fake.close()

    def fake(self, lines, sleep=60):
        fake = FakeCloudflared(lines, sleep)
        self.fakes.append(fake)
        return fake

    def test_returns_url_and_stop_ends_the_process(self):
        proc, url = tunnel.start(8765, timeout=10, exe=self.fake([BANNER]).path)
        try:
            self.assertEqual(url, "https://abc-def-ghi.trycloudflare.com")
            self.assertIsNone(proc.poll())
        finally:
            tunnel.stop(proc)
        self.assertIsNotNone(proc.poll())

    def test_exit_without_url_raises_with_the_output(self):
        with self.assertRaises(tunnel.TunnelError) as ctx:
            tunnel.start(8765, timeout=10, exe=self.fake(["INF something went wrong"], sleep=0).path)
        self.assertIn("something went wrong", str(ctx.exception))

    def test_silence_past_the_timeout_raises(self):
        with self.assertRaises(tunnel.TunnelError):
            tunnel.start(8765, timeout=1, exe=self.fake(["INF still starting"]).path)

    def test_missing_cloudflared_raises(self):
        with mock.patch.object(tunnel, "find_cloudflared", return_value=None):
            with self.assertRaises(tunnel.TunnelError):
                tunnel.start(8765)


if __name__ == "__main__":
    unittest.main()
