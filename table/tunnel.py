"""A public HTTPS address for this table through a Cloudflare quick tunnel (standard library only).

    proc, url = tunnel.start(8765)   # runs `cloudflared tunnel --url http://127.0.0.1:8765`
    tunnel.stop(proc)

cloudflared dials out to Cloudflare; nothing on this computer listens beyond 127.0.0.1.
"""
import collections
import re
import shutil
import subprocess
import threading

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelError(RuntimeError):
    pass


def find_cloudflared():
    return shutil.which("cloudflared")


def parse_url(line):
    """The trycloudflare.com address in one line of cloudflared output, or None."""
    m = URL_RE.search(line)
    return m.group(0) if m else None


def start(port, timeout=30, exe=None):
    """Start a quick tunnel to 127.0.0.1:port. Returns (proc, url) once cloudflared prints the address."""
    exe = exe or find_cloudflared()
    if not exe:
        raise TunnelError("cloudflared is not installed")
    proc = subprocess.Popen(
        [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    found = {}
    done = threading.Event()  # set when the address appears or cloudflared closes its output
    recent = collections.deque(maxlen=8)

    def pump():
        # Read until the process ends, so cloudflared never blocks on a full pipe.
        for line in proc.stdout:
            line = line.rstrip()
            recent.append(line)
            if "url" not in found:
                url = parse_url(line)
                if url:
                    found["url"] = url
                    done.set()
        proc.stdout.close()
        done.set()

    threading.Thread(target=pump, daemon=True).start()
    done.wait(timeout)
    if found.get("url"):
        return proc, found["url"]
    stop(proc)
    tail = "\n    ".join(recent) or "(no output)"
    raise TunnelError(f"cloudflared gave no address:\n    {tail}")


def stop(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
