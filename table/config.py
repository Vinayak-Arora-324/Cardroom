"""Settings persisted to settings.json (git-ignored) next to server.py."""
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("AITABLE_SETTINGS", os.path.join(ROOT, "settings.json"))

DEFAULTS = {
    "openrouter_api_key": "",
    "openrouter_default_model": "openai/gpt-4o-mini",
    "claude_path": "",
    "codex_path": "",
    "grok_path": "",
    "claude_default_model": "sonnet",
    "codex_default_model": "",
    "grok_default_model": "",
    "claude_force_subscription": True,   # strip ANTHROPIC_API_KEY from the CLI's env
    "codex_force_subscription": True,    # strip OPENAI_API_KEY from the CLI's env
    "grok_force_subscription": True,     # strip XAI_API_KEY from the CLI's env
    "claude_structured_output": True,
    "codex_structured_output": True,
    "grok_structured_output": True,
    "max_parallel_claude": 4,
    "max_parallel_codex": 4,
    "max_parallel_grok": 4,
    "max_parallel_openrouter": 6,
    "agent_timeout": 150,
    "ai_pace_seconds": 1.0,               # pause after each AI action so you can follow along
    "reveal_ai_cards": False,             # spectator / debug mode
    "parallel_blackjack": True,           # AI blackjack hands are played simultaneously
}

_lock = threading.Lock()
_settings = None


def _load():
    data = dict(DEFAULTS)
    if os.path.exists(PATH):
        try:
            with open(PATH) as f:
                data.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
    return data


def get():
    global _settings
    with _lock:
        if _settings is None:
            _settings = _load()
        return dict(_settings)


def update(changes):
    global _settings
    with _lock:
        if _settings is None:
            _settings = _load()
        for k, v in changes.items():
            if k not in DEFAULTS:
                continue
            if k == "openrouter_api_key" and (v is None or "•" in str(v)):
                continue  # masked value echoed back from the UI: keep the stored key
            default = DEFAULTS[k]
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int):
                v = int(v)
            elif isinstance(default, float):
                v = float(v)
            else:
                v = str(v).strip()
            _settings[k] = v
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_settings, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, PATH)
        return dict(_settings)


def openrouter_key():
    return get().get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY", "")


def public():
    """Settings safe to send to the browser (API key masked)."""
    s = get()
    key = s.get("openrouter_api_key", "")
    s["openrouter_api_key"] = ("•" * 8 + key[-4:]) if key else ""
    s["openrouter_key_from_env"] = bool(not key and os.environ.get("OPENROUTER_API_KEY"))
    return s
