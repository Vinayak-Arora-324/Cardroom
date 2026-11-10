"""AI backends: Claude Code CLI, Codex CLI, Grok Build CLI, OpenRouter HTTP API, and a local bot.

Every AI seat gets its own subprocess / HTTP request per decision, so any number
of Claude Code, Codex or Grok seats can be at the table (and think) at the same time.
Per-provider semaphores cap how many run concurrently, which protects your
subscription rate limits and your laptop's RAM.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from . import config

BACKENDS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "grok": "Grok",
    "openrouter": "OpenRouter",
    "bot": "Local bot",
}

_semaphores = {}
_sem_lock = threading.Lock()


def _semaphore(kind):
    limit = max(1, int(config.get().get(f"max_parallel_{kind}", 4)))
    with _sem_lock:
        cur = _semaphores.get(kind)
        if cur is None or cur[0] != limit:
            cur = (limit, threading.BoundedSemaphore(limit))
            _semaphores[kind] = cur
        return cur[1]


class AgentError(Exception):
    pass


# --------------------------------------------------------------------------- #
# JSON extraction
# --------------------------------------------------------------------------- #
def extract_json(text):
    """Pull the first JSON object out of a model reply."""
    if text is None:
        raise AgentError("empty reply")
    if isinstance(text, dict):
        return text
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise AgentError(f"no JSON object in reply: {text[:200]!r}")


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
def _subprocess_env(strip_keys):
    env = dict(os.environ)
    for k in strip_keys:
        env.pop(k, None)
    return env


def _run(cmd, stdin_text, cwd, timeout, env):
    try:
        proc = subprocess.run(
            cmd, input=stdin_text, capture_output=True, text=True,
            cwd=cwd, timeout=timeout, env=env,
        )
    except FileNotFoundError:
        raise AgentError(f"command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise AgentError(f"timed out after {timeout}s")
    return proc


def call_claude(seat, system, prompt, schema):
    s = config.get()
    exe = s.get("claude_path") or "claude"
    cmd = [
        exe, "-p",
        "--output-format", "json",
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--system-prompt", system,
    ]
    if seat.get("model"):
        cmd += ["--model", seat["model"]]
    if seat.get("effort"):
        cmd += ["--effort", seat["effort"]]
    if s.get("claude_structured_output", True) and schema:
        cmd += ["--json-schema", json.dumps(schema)]
    # Strip API keys so the CLI uses your Claude subscription login.
    strip = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"] if s.get("claude_force_subscription", True) else []
    with tempfile.TemporaryDirectory(prefix="aitable-claude-") as tmp:
        proc = _run(cmd, prompt, tmp, s.get("agent_timeout", 150), _subprocess_env(strip))
    out = proc.stdout.strip()
    if not out:
        err = proc.stderr.strip()
        if "login" in err.lower() or "auth" in err.lower():
            raise AgentError("Claude Code is not logged in: run `claude` once in a terminal and use /login")
        raise AgentError(f"claude exited {proc.returncode}: {err[:300]}")
    try:
        data = json.loads(out.splitlines()[-1])
    except json.JSONDecodeError:
        return extract_json(out), {}
    if data.get("is_error"):
        raise AgentError(f"claude error: {str(data.get('result'))[:300]}")
    meta = {"cost_usd": data.get("total_cost_usd")}
    if isinstance(data.get("structured_output"), dict):
        return data["structured_output"], meta
    return extract_json(data.get("result", "")), meta


def call_codex(seat, system, prompt, schema):
    s = config.get()
    exe = s.get("codex_path") or "codex"
    with tempfile.TemporaryDirectory(prefix="aitable-codex-") as tmp:
        out_file = os.path.join(tmp, "last.txt")
        cmd = [
            exe, "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox", "read-only",
            "--color", "never",
            "-C", tmp,
            "-o", out_file,
        ]
        if seat.get("model"):
            cmd += ["-m", seat["model"]]
        if seat.get("effort"):
            cmd += ["-c", f'model_reasoning_effort="{seat["effort"]}"']
        if s.get("codex_structured_output", True) and schema:
            schema_file = os.path.join(tmp, "schema.json")
            with open(schema_file, "w") as f:
                json.dump(schema, f)
            cmd += ["--output-schema", schema_file]
        cmd.append("-")  # read prompt from stdin
        strip = ["OPENAI_API_KEY"] if s.get("codex_force_subscription", True) else []
        full = f"{system}\n\n---\n\n{prompt}\n\nDo not run any shell commands or read files. Reply with only the JSON object."
        proc = _run(cmd, full, tmp, s.get("agent_timeout", 150), _subprocess_env(strip))
        text = ""
        if os.path.exists(out_file):
            with open(out_file) as f:
                text = f.read()
    if not text.strip():
        err = (proc.stderr or proc.stdout).strip()
        if "401" in err or "Unauthorized" in err or "login" in err.lower():
            raise AgentError("Codex is not logged in: run `codex login` (choose 'Sign in with ChatGPT') in a terminal")
        raise AgentError(f"codex exited {proc.returncode}: {err[-300:]}")
    return extract_json(text), {}


def call_grok(seat, system, prompt, schema):
    s = config.get()
    exe = s.get("grok_path") or "grok"
    with tempfile.TemporaryDirectory(prefix="aitable-grok-") as tmp:
        prompt_file = os.path.join(tmp, "prompt.txt")
        with open(prompt_file, "w") as f:
            f.write(prompt)
        cmd = [
            exe,
            "--prompt-file", prompt_file,
            "--verbatim",
            "--output-format", "json",
            "--system-prompt-override", system,
            # An empty --tools list leaves every tool on, so allow one harmless tool instead.
            "--tools", "todo_write",
            "--disallowed-tools", "search_tool,use_tool",
            "--no-subagents",
            "--disable-web-search",
            "--max-turns", "3",
            "--cwd", tmp,
        ]
        if seat.get("model"):
            cmd += ["-m", seat["model"]]
        if seat.get("effort"):
            cmd += ["--effort", seat["effort"]]
        if s.get("grok_structured_output", True) and schema:
            cmd += ["--json-schema", json.dumps(schema)]
        # Strip the API key so the CLI uses your grok.com login.
        strip = ["XAI_API_KEY"] if s.get("grok_force_subscription", True) else []
        proc = _run(cmd, "", tmp, s.get("agent_timeout", 150), _subprocess_env(strip))
    out = proc.stdout.strip()
    try:
        data = json.loads(out) if out else None
    except json.JSONDecodeError:
        return extract_json(out), {}
    if not isinstance(data, dict) or data.get("type") == "error":
        err = (data or {}).get("message") or proc.stderr.strip()
        low = err.lower()
        if "login" in low or "sign in" in low or "auth" in low or "401" in err:
            raise AgentError("Grok is not logged in: run `grok login` in a terminal")
        raise AgentError(f"grok exited {proc.returncode}: {err[-300:]}")
    meta = {"cost_usd": data.get("total_cost_usd")}
    if isinstance(data.get("structuredOutput"), dict):
        return data["structuredOutput"], meta
    return extract_json(data.get("text", "")), meta


def call_openrouter(seat, system, prompt, schema):
    s = config.get()
    key = config.openrouter_key()
    if not key:
        raise AgentError("no OpenRouter API key set (Settings, or OPENROUTER_API_KEY env var)")
    model = seat.get("model") or s.get("openrouter_default_model") or "openai/gpt-4o-mini"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt + "\n\nReply with only the JSON object."},
        ],
        "temperature": 0.8,
        "usage": {"include": True},
    }
    if seat.get("effort"):
        body["reasoning"] = {"effort": seat["effort"]}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost/ai-card-table",
            "X-Title": "AI Card Table",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=s.get("agent_timeout", 150)) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise AgentError(f"OpenRouter HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        raise AgentError(f"OpenRouter unreachable: {e.reason}")
    if "error" in data:
        raise AgentError(f"OpenRouter: {data['error']}")
    content = data["choices"][0]["message"].get("content") or ""
    return extract_json(content), {"cost_usd": (data.get("usage") or {}).get("cost")}


CALLERS = {"claude": call_claude, "codex": call_codex, "grok": call_grok, "openrouter": call_openrouter}


def ask(seat, system, prompt, schema):
    """Run one decision for a seat. Returns (reply_dict, meta). Raises AgentError."""
    kind = seat["kind"]
    caller = CALLERS.get(kind)
    if caller is None:
        raise AgentError(f"unknown backend {kind}")
    with _semaphore(kind):
        t0 = time.time()
        reply, meta = caller(seat, system, prompt, schema)
        meta["seconds"] = round(time.time() - t0, 1)
    if not isinstance(reply, dict):
        raise AgentError("reply was not a JSON object")
    return reply, meta


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def detect_clis():
    s = config.get()
    found = {}
    for kind, default in (("claude", "claude"), ("codex", "codex"), ("grok", "grok")):
        exe = s.get(f"{kind}_path") or default
        path = shutil.which(exe)
        version = None
        if path:
            try:
                version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
            except Exception:  # noqa: BLE001
                version = "?"
        found[kind] = {"path": path, "version": version}
    found["openrouter"] = {"key_set": bool(config.openrouter_key())}
    return found


TEST_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "table_talk": {"type": "string"}},
    "required": ["ok", "table_talk"],
    "additionalProperties": False,
}


def test_seat(seat):
    if seat["kind"] == "bot":
        return {"ok": True, "message": "Local bot is always available.", "seconds": 0}
    try:
        reply, meta = ask(
            seat,
            "You are a card player sitting down at a friendly table. Reply only with JSON.",
            'Say a short hello to the table. Reply as {"ok": true, "table_talk": "<one short sentence>"}.',
            TEST_SCHEMA,
        )
        return {"ok": True, "message": str(reply.get("table_talk", reply))[:200], "seconds": meta.get("seconds")}
    except AgentError as e:
        return {"ok": False, "message": str(e)}


CLAUDE_MODELS = ["haiku", "sonnet", "opus", "fable"]
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
BASIC_EFFORTS = ["low", "medium", "high"]


def _codex_models():
    """Models this Codex login offers, read from the cache the CLI keeps; [] if unavailable."""
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    try:
        with open(os.path.join(home, "models_cache.json")) as f:
            models = json.load(f)["models"]
        return [
            {"id": m["slug"], "efforts": [e["effort"] for e in m["supported_reasoning_levels"]]}
            for m in models if m.get("visibility") == "list"
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return []


EFFORT_ORDER = ["minimal", "low", "medium", "high", "xhigh", "max"]


def _grok_models():
    """Models this Grok login offers, read from the cache the CLI keeps; [] if unavailable."""
    home = os.environ.get("GROK_HOME") or os.path.expanduser("~/.grok")
    try:
        with open(os.path.join(home, "models_cache.json")) as f:
            models = json.load(f)["models"]
        out = []
        for slug, m in models.items():
            info = m.get("info") or {}
            if info.get("hidden"):
                continue
            efforts = [e["value"] for e in info.get("reasoning_efforts") or []] if info.get("supports_reasoning_effort") else []
            efforts.sort(key=lambda e: EFFORT_ORDER.index(e) if e in EFFORT_ORDER else len(EFFORT_ORDER))
            out.append({"id": info.get("id") or slug, "efforts": efforts})
        return out
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def model_catalog():
    """What the lobby's model and effort dropdowns offer for each backend."""
    codex = _codex_models()
    # "Default model" can resolve to any of them, so offer only the levels they all accept.
    codex_efforts = [e for e in codex[0]["efforts"] if all(e in m["efforts"] for m in codex)] if codex else BASIC_EFFORTS
    grok = _grok_models()
    grok_efforts = [e for e in grok[0]["efforts"] if all(e in m["efforts"] for m in grok)] if grok else BASIC_EFFORTS
    return {
        "claude": {"models": [{"id": m} for m in CLAUDE_MODELS], "efforts": CLAUDE_EFFORTS},
        "codex": {"models": codex, "efforts": codex_efforts},
        "grok": {"models": grok, "efforts": grok_efforts},
        "openrouter": {"models": [], "efforts": BASIC_EFFORTS},
    }


_models_cache = {"t": 0, "data": []}


def openrouter_models():
    if time.time() - _models_cache["t"] < 3600 and _models_cache["data"]:
        return _models_cache["data"]
    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=20) as resp:
            data = json.loads(resp.read())["data"]
        models = sorted(m["id"] for m in data)
        _models_cache.update(t=time.time(), data=models)
        return models
    except Exception:  # noqa: BLE001
        return _models_cache["data"]
