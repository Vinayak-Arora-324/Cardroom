"""Shared table machinery: players, state versioning, human input, AI decisions."""
import queue
import threading
import time
from collections import deque

from . import agents, config


class GameStopped(Exception):
    pass


def pick_option(options):
    """Validator for a human request that is a choice between fixed options."""
    def validate(data):
        choice = data.get("action")
        if choice not in options:
            raise ValueError(f"choose one of {', '.join(options)}")
        return choice
    return validate


class Player:
    def __init__(self, pid, spec, chips):
        self.id = pid
        self.kind = spec.get("kind", "bot")           # human | claude | codex | openrouter | bot
        self.name = (spec.get("name") or "").strip()[:24] or f"Player {pid + 1}"
        self.model = (spec.get("model") or "").strip()
        self.effort = (spec.get("effort") or "").strip()
        self.personality = (spec.get("personality") or "").strip()[:300]
        self.chips = chips
        self.notes = ""             # private memory the AI carries between hands
        self.thinking_since = None  # epoch seconds while an AI call is running
        self.stats = {"calls": 0, "seconds": 0.0, "cost_usd": 0.0, "fallbacks": 0, "last_error": ""}

    @property
    def is_human(self):
        return self.kind == "human"

    def seat_cfg(self):
        return {"kind": self.kind, "model": self.model, "effort": self.effort}

    def public(self):
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "backend": "You" if self.is_human else agents.BACKENDS.get(self.kind, self.kind),
            "model": self.model,
            "effort": self.effort,
            "chips": self.chips,
            "thinking": self.thinking_since is not None,
            "thinking_for": round(time.time() - self.thinking_since, 1) if self.thinking_since else 0,
            "stats": {
                **self.stats,
                "seconds": round(self.stats["seconds"], 1),
                "cost_usd": round(self.stats["cost_usd"], 4),
                "avg_seconds": round(self.stats["seconds"] / self.stats["calls"], 1) if self.stats["calls"] else 0,
            },
        }


class BaseGame:
    game_name = "base"
    talk_rule = "keep it under 20 words, empty string to stay quiet; never reveal your hole cards honestly unless bluffing is your thing"

    def __init__(self, player_specs, options):
        self.options = options
        start = int(options.get("starting_chips", 1000))
        self.players = [Player(i, spec, start) for i, spec in enumerate(player_specs)]
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.version = 0
        self.log = deque(maxlen=300)
        self.chat = deque(maxlen=60)
        self.status = "Starting…"
        self.human_request = None
        self._human_q = queue.Queue()
        self.stopped = False
        self.finished = False
        self.thread = None
        self.hand_no = 0

    # ---- state / notification ---------------------------------------------
    def changed(self):
        with self.cond:
            self.version += 1
            self.cond.notify_all()

    def wait_version(self, since, timeout=20):
        with self.cond:
            if self.version <= since:
                self.cond.wait(timeout)
            return self.version

    def add_log(self, text, kind="info"):
        if text.startswith("You "):  # "You bets 50" -> "You bet 50"
            word, _, rest = text[4:].partition(" ")
            word = {"has": "have", "passes": "pass"}.get(word, word[:-1] if word.endswith("s") else word)
            text = f"You {word} {rest}".rstrip()
        with self.lock:
            self.log.append({"t": time.time(), "text": text, "kind": kind})
        self.changed()

    def set_status(self, text):
        self.status = text
        self.changed()

    def say(self, player, text):
        text = (text or "").strip().replace("\n", " ")[:220]
        if not text:
            return
        with self.lock:
            self.chat.append({"name": player.name, "pid": player.id, "text": text, "t": time.time()})
        self.changed()

    def recent_chat(self, n=10):
        lines = list(self.chat)[-n:]
        return "\n".join(f'{self.spoken_name(self.players[c["pid"]])}: "{c["text"]}"' for c in lines) or "(quiet table)"

    def spoken_name(self, player):
        """How AI prompts refer to a player, so a human seat called "You" isn't read as the AI itself."""
        return "the human player" if player.is_human and player.name == "You" else player.name

    def check_stopped(self):
        if self.stopped:
            raise GameStopped()

    def sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.check_stopped()
            time.sleep(min(0.1, max(0, end - time.time())))

    def pace(self):
        self.sleep(float(config.get().get("ai_pace_seconds", 1.0)))

    # ---- lifecycle ----------------------------------------------------------
    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"{self.game_name}-loop")
        self.thread.start()

    def stop(self):
        self.stopped = True
        self._human_q.put(None)
        self.changed()

    def _run(self):
        try:
            self.run()
        except GameStopped:
            self.status = "Game stopped."
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.add_log(f"Game crashed: {e!r}", "error")
            self.status = f"Error: {e}"
        self.finished = True
        self.human_request = None
        self.changed()

    def run(self):
        raise NotImplementedError

    def run_parallel(self, tasks):
        """Run callables in threads (AI seats thinking at the same time); re-raise the first error."""
        errors = []

        def wrap(fn):
            try:
                fn()
            except GameStopped:
                pass
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                errors.append(e)

        threads = [threading.Thread(target=wrap, args=(fn,), daemon=True) for fn in tasks]
        for t in threads:
            t.start()
        return threads, errors

    def join_parallel(self, handle):
        threads, errors = handle
        for t in threads:
            while t.is_alive():
                t.join(0.2)
                self.check_stopped()
        if errors:
            raise errors[0]

    # ---- human input ----------------------------------------------------------
    def ask_human(self, request, validate):
        """Block until the browser submits a valid answer. validate(data) -> normalized or raises ValueError."""
        while True:
            self.check_stopped()
            with self.lock:
                self.human_request = request
            self.changed()
            data = self._human_q.get()
            self.check_stopped()
            if data is None:
                continue
            try:
                result = validate(data)
            except ValueError as e:
                request = {**request, "error": str(e)}
                continue
            with self.lock:
                self.human_request = None
            self.changed()
            return result

    def submit_human(self, data):
        if self.human_request is None:
            raise ValueError("It's not your turn.")
        self._human_q.put(data)

    def human_chat(self, text):
        human = next((p for p in self.players if p.is_human), None)
        if human:
            self.say(human, text)

    # ---- AI decisions ---------------------------------------------------------
    def character_brief(self, player):
        """The part of the system prompt that says who this seat is and what it's playing for."""
        persona = player.personality or "You are a sharp, sociable card player with a dry sense of humour."
        return f"Play to win chips.\nPersonality: {persona}"

    def system_prompt(self, player, game_rules):
        return (
            f"You are \"{player.name}\", an AI player at a casual {self.game_name} table on someone's laptop. "
            f"The other players are a human and other AI agents (Claude Code, Codex, other models). "
            f"{self.character_brief(player)}\n\n{game_rules}\n\n"
            "Respond with ONE JSON object only, matching the requested schema. "
            f"\"table_talk\" is optional banter everyone hears ({self.talk_rule}). "
            "\"notes\" is your private memory for future hands (max 300 chars: opponent tendencies, reads, plans); it replaces your previous notes."
        )

    def ai_decide(self, player, system, prompt, schema, validate, fallback):
        """Ask an AI seat; validate; retry once; fall back to the local bot. Returns normalized decision."""
        self.check_stopped()
        if player.kind == "bot":
            self.sleep(min(0.4, float(config.get().get("ai_pace_seconds", 1.0))))
            return fallback()
        with self.lock:
            player.thinking_since = time.time()
        self.changed()
        error = None
        try:
            for attempt in range(2):
                self.check_stopped()
                p = prompt if error is None else f"{prompt}\n\nYOUR PREVIOUS REPLY WAS INVALID: {error}\nTry again."
                try:
                    reply, meta = agents.ask(player.seat_cfg(), system, p, schema)
                except agents.AgentError as e:
                    error = str(e)
                    player.stats["last_error"] = error[:300]
                    self.add_log(f"{player.name}: {error}", "error")
                    if "not found" in error or "no OpenRouter API key" in error:
                        break  # retrying won't help
                    continue
                player.stats["calls"] += 1
                player.stats["seconds"] += meta.get("seconds", 0)
                player.stats["cost_usd"] += meta.get("cost_usd") or 0
                self.check_stopped()
                try:
                    decision = validate(reply)
                except ValueError as e:
                    error = f"{e}. Reply was {str(reply)[:200]}"
                    continue
                self.say(player, reply.get("table_talk", ""))
                if isinstance(reply.get("notes"), str) and reply["notes"].strip():
                    player.notes = reply["notes"].strip()[:400]
                return decision
        finally:
            with self.lock:
                player.thinking_since = None
            self.changed()
        player.stats["fallbacks"] += 1
        self.add_log(f"{player.name} couldn't answer; the local bot played for them.", "warn")
        return fallback()

    # ---- snapshot -------------------------------------------------------------
    def snapshot(self):
        with self.lock:
            return {
                "game": self.game_name,
                "version": self.version,
                "status": self.status,
                "hand_no": self.hand_no,
                "finished": self.finished,
                "players": [p.public() for p in self.players],
                "log": list(self.log)[-80:],
                "chat": list(self.chat)[-40:],
                "human_request": self.human_request,
                "options": self.options,
                **self.game_state(),
            }

    def game_state(self):
        return {}

    def show_cards_of(self, player, revealed=False):
        return player.is_human or revealed or config.get().get("reveal_ai_cards")
