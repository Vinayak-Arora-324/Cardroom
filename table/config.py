"""Settings persisted to settings.json (git-ignored) next to server.py."""
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("AITABLE_SETTINGS", os.path.join(ROOT, "settings.json"))

DEFAULTS = {
    "claude_path": "",
    "codex_path": "",
    "grok_path": "",
    "opencode_path": "",
    "claude_default_model": "sonnet",
    "codex_default_model": "",
    "grok_default_model": "",
    "opencode_default_model": "",         # provider/model, e.g. openrouter/openrouter/free
    "claude_force_subscription": True,   # strip ANTHROPIC_API_KEY from the CLI's env
    "codex_force_subscription": True,    # strip OPENAI_API_KEY from the CLI's env
    "grok_force_subscription": True,     # strip XAI_API_KEY from the CLI's env
    "claude_structured_output": True,
    "codex_structured_output": True,
    "grok_structured_output": True,
    "max_parallel_claude": 4,
    "max_parallel_codex": 4,
    "max_parallel_grok": 4,
    "max_parallel_opencode": 4,
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
                saved = json.load(f)
            # Drop settings from older versions (such as a stored OpenRouter key).
            data.update({k: v for k, v in saved.items() if k in DEFAULTS})
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


def public():
    """Settings to send to the browser."""
    return get()
