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

## Play with friends

Each human seat gets its own invite link from the lobby. A friend opens it and sits in that seat. They can play and chat, but they can't change settings, start games or use your AI accounts.

**Anywhere: `./run.sh --tunnel`** (recommended)

```bash
sudo pacman -S cloudflared    # Arch; elsewhere, see Cloudflare's download page
./run.sh --tunnel
```

The terminal prints an `https://….trycloudflare.com` address for your friends. The address is new every run, so invite links are new every run too. Cloudflare carries the traffic, so Cloudflare can see it. That's fine for a card game. Nothing on your machine listens beyond this computer.

**Private table: Tailscale.** Only people on your tailnet can reach the table. Start the tailnet proxy, then run Cardroom with its address (`tailscale serve status` shows it):

```bash
tailscale serve --bg 8765
./run.sh --public-url https://<machine>.<tailnet>.ts.net
```

**Same Wi-Fi: `./run.sh --lan`.** Friends use the LAN address the terminal prints. This is plain, unencrypted HTTP, so only use it on a home network you trust.

Without one of these flags, Cardroom only answers on this computer.

- **New link** (in the invite dialog) cancels a link that was shared by mistake. Use it too when a friend switches device. Each seat link works on the first device that opens it.
- **Never share the host link** (the one with `#host=`, which the terminal prints and opens). It controls your table and your accounts. Never open it on a tunnel or proxy address.
- **Turn timer:** set a turn timer in the lobby so a slow friend doesn't stall the table. When time runs out, the seat is played automatically.
