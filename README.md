# Cardroom

Play Texas Hold'em, Blackjack or Contract Bridge in your browser against AI agents: Claude Code, Codex, Grok, any model OpenCode can reach, or free local bots. Mix them however you like, or sit back and watch them play each other.

A Python engine owns the deck and the rules. The AIs only choose moves and see only their own cards, so they can't cheat.

## Run it

```bash
./run.sh
```

## AI players

| Seat | Setup | Billing |
|---|---|---|
| Claude Code | run `claude`, then `/login` | Claude subscription |
| Codex | `npm i -g @openai/codex`, then `codex login` | ChatGPT subscription |
| Grok | install Grok Build, then `grok login` | Grok subscription |
| OpenCode | install `opencode`, then `opencode auth login` for each provider (OpenRouter, etc.) | that provider; models marked *free* cost nothing |
| Local bot | none | free |

Each seat gets its own model and effort level. **Test** checks a seat before you play.

In Hold'em, each AI or bot seat can also be given a personality, or left on **Random** to keep it secret. Personalities vary from seat to seat and shift with how the night is going.

> Cardroom listens on `127.0.0.1` only. Don't expose it to a network: it runs CLIs logged in to your accounts.
