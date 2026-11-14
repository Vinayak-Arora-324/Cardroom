"""AI backends: Claude Code CLI, Codex CLI, Grok Build CLI, OpenCode CLI, and a local bot.

Every AI seat gets its own subprocess per decision, so any number of Claude Code,
Codex, Grok or OpenCode seats can be at the table (and think) at the same time.
Per-provider semaphores cap how many run concurrently, which protects your
subscription rate limits and your laptop's RAM.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from . import config

BACKENDS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "grok": "Grok",
    "opencode": "OpenCode",
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


_live = set()          # CLI processes running right now
_live_lock = threading.Lock()


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()


def kill_running():
    """Kill every CLI still thinking (they run in their own process groups, so Ctrl+C misses them)."""
    with _live_lock:
        procs = list(_live)
    for proc in procs:
        _kill_group(proc)


def _run(cmd, stdin_text, cwd, timeout, env):
    try:
        # Own process group, so a timeout also kills anything the CLI started (wrappers, servers).
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd, env=env, start_new_session=True,
        )
    except FileNotFoundError:
        raise AgentError(f"command not found: {cmd[0]}")
    with _live_lock:
        _live.add(proc)
    try:
        out, err = proc.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.communicate()
        raise AgentError(f"timed out after {timeout}s")
    finally:
        with _live_lock:
            _live.discard(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


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
            # Table chat can carry prompts from guests, so the seat gets no tools at all. Read-only
            # sandboxing alone still lets the shell read any file, so turn off the shell and exec tools,
            # web search, and the user's config (which loads plugins and MCP servers) as well.
            "--ignore-user-config",
            "--disable", "shell_tool",
            "--disable", "unified_exec",
            "-c", 'web_search="disabled"',
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


OPENCODE_AGENT = "cardroom"


def call_opencode(seat, system, prompt, schema):
    s = config.get()
    exe = s.get("opencode_path") or "opencode"
    # A private agent for this one run: the table's system prompt and no tools at all.
    agent = {
        "mode": "primary",
        "description": "Card player at the AI Card Table",
        "prompt": system,
        "tools": {"*": False},
        "permission": {"*": "deny"},
    }
    env = _subprocess_env([])
    env.update({
        "OPENCODE_CONFIG_CONTENT": json.dumps({"agent": {OPENCODE_AGENT: agent}}),
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",   # don't pull in CLAUDE.md / Claude skills
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
    })
    cmd = [
        exe, "run",
        # A private server reads the agent above; the shared background service would not.
        "--standalone",
        "--archived",
        "--format", "json",
        "--agent", OPENCODE_AGENT,
        "--title", "AI Card Table",
    ]
    model = seat.get("model") or ""
    if model and seat.get("effort"):
        model = f"{model.split('#')[0]}#{seat['effort']}"
    if model:
        cmd += ["-m", model]
    full = prompt + "\n\nReply with only the JSON object."
    if schema:
        full += f" It must match this JSON schema: {json.dumps(schema)}"
    with tempfile.TemporaryDirectory(prefix="aitable-opencode-") as tmp:
        proc = _run(cmd, full, tmp, s.get("agent_timeout", 150), env)
    texts, cost, error, last_msg = {}, 0.0, None, None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "text" and isinstance(part.get("text"), str):
            if part.get("messageID") != last_msg:  # only the final message's text counts
                texts, last_msg = {}, part.get("messageID")
            texts[part.get("id")] = part["text"]
        elif event.get("type") == "step_finish":
            cost += part.get("cost") or 0
        elif event.get("type") == "error":
            err = event.get("error") or {}
            error = (err.get("message") or str(err)) if isinstance(err, dict) else str(err)
    text = "".join(texts.values())
    if not text.strip():
        err = error or proc.stderr.strip() or proc.stdout.strip()
        low = err.lower()
        if "api key" in low or "unauthorized" in low or "401" in err or "auth" in low:
            raise AgentError(f"OpenCode is not signed in to this model's provider: run `opencode auth login` ({err[:200]})")
        raise AgentError(f"opencode exited {proc.returncode}: {err[-300:]}")
    return extract_json(text), {"cost_usd": cost}


CALLERS = {"claude": call_claude, "codex": call_codex, "grok": call_grok, "opencode": call_opencode}


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
    for kind in ("claude", "codex", "grok", "opencode"):
        default = kind
        exe = s.get(f"{kind}_path") or default
        path = shutil.which(exe)
        version = None
        if path:
            try:
                version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
            except Exception:  # noqa: BLE001
                version = "?"
        found[kind] = {"path": path, "version": version}
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


_opencode_cache = {"t": 0, "exe": None, "data": []}


def _opencode_is_free(m):
    """Zero-priced models whose id says free (routers like openrouter/auto list $0 but bill)."""
    costs = m.get("cost") or []
    zero = bool(costs) and all(not c.get("input") and not c.get("output") for c in costs)
    return zero and ("free" in m["id"].lower() or m.get("providerID") == "opencode")


def _opencode_models():
    """Models OpenCode can reach (with their effort variants); [] if unavailable. Cached for 10 min."""
    exe = config.get().get("opencode_path") or "opencode"
    c = _opencode_cache
    if c["exe"] == exe and time.time() - c["t"] < 600 and c["data"]:
        return c["data"]
    if not shutil.which(exe):
        return []
    out = []
    try:
        proc = subprocess.run([exe, "api", "GET", "/api/model"], capture_output=True, text=True, timeout=20)
        for m in json.loads(proc.stdout)["data"]:
            if not m.get("enabled", True) or m.get("status") == "deprecated":
                continue
            out.append({
                "id": f"{m['providerID']}/{m['id']}",
                "efforts": [v["id"] for v in m.get("variants") or []],
                "free": _opencode_is_free(m),
            })
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        try:  # older builds: plain list, no effort variants
            proc = subprocess.run([exe, "models"], capture_output=True, text=True, timeout=20)
            out = [{"id": line.strip(), "efforts": [], "free": "free" in line.lower()}
                   for line in proc.stdout.splitlines() if "/" in line]
        except (OSError, subprocess.SubprocessError):
            out = []
    out.sort(key=lambda m: (not m["free"], m["id"]))
    if out:
        c.update(t=time.time(), exe=exe, data=out)
    return out


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
        # Effort variants differ per model, so "default model" offers none.
        "opencode": {"models": _opencode_models(), "efforts": []},
    }
