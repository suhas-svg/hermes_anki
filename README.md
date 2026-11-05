# Anki Bridge for Hermes

Turn notes into Anki cards, find what is due, and run a quiz without leaving
Hermes. The plugin talks to [AnkiConnect](https://ankiweb.net/shared/info/2055492159)
on your own machine, so cards stay in your collection and nothing is uploaded.

```
You: Turn these three facts into cards in my Spanish deck.
Hermes: Here are the cards I would add: ...
You: Looks good.
Hermes: Added 3 notes to Spanish.
You: Quiz me on what is due.
Hermes: ¿Qué significa "aprovechar"?
You: to take advantage of
Hermes: Correct. Graded good — next review in 4 days.
```

## What it adds

| Surface | What you get |
| --- | --- |
| Tools | `anki_decks`, `anki_add_cards`, `anki_search`, `anki_due`, `anki_answer` |
| Slash command | `/anki status`, `/anki due [deck]`, `/anki help` |
| CLI | `hermes anki status`, `hermes anki decks`, `hermes anki search QUERY` |
| Skill | `anki:study` — card-writing rules and a safe quiz loop |

The tools return structured JSON with plain-text fields, so the model can add
cards, search with Anki's own query syntax, show a question without leaking the
answer, and grade the card afterwards. The bundled skill teaches the card
quality rules; the plugin does the actual work.

## Requirements

- Hermes Agent 0.22.5 or newer.
- [Anki desktop](https://apps.ankiweb.net/) (the free desktop app, not AnkiWeb).
- The [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on
  (code `2055492159`).

No Python dependencies. The client uses the standard library only.

## Install

```bash
hermes plugins install suhas-svg/hermes_anki
```

Once the plugin is in the curated catalog you can also run:

```bash
hermes plugins install anki
```

### Set up AnkiConnect

1. Open Anki desktop.
2. Go to **Tools → Add-ons → Get Add-ons**.
3. Paste `2055492159`, install it, and restart Anki.
4. Leave Anki open. AnkiConnect listens on `http://127.0.0.1:8765`.

Verify the bridge:

```bash
hermes anki status
```

You should see the deck count, scheduled due cards, new cards, and reviews
completed today. If Anki or the add-on is not running, the command explains
what to fix instead of printing a traceback.

## Use it

Ask Hermes in ordinary language:

- “Turn these notes into five Anki cards in my Biology deck.”
- “What is due in Spanish today?”
- “Search my collection for cards tagged verbs.”
- “Quiz me on the due cards, one at a time.”

Or use the slash command:

```
/anki status
/anki due Spanish
```

## Configuration

Open **Settings → Plugins → Anki** in the Hermes desktop app, or edit
`plugins.entries.anki.settings` in `config.yaml`.

| Key | Default | Purpose |
| --- | --- | --- |
| `anki_url` | `http://127.0.0.1:8765` | AnkiConnect endpoint. Keep the loopback default unless Anki runs elsewhere. |
| `default_deck` | `Default` | Deck used when a tool call omits one. |
| `default_model` | `Basic` | Note model used when a tool call omits one. |
| `request_timeout` | `10.0` | Seconds to wait for AnkiConnect (clamped to 1–120). |
| `max_cards` | `50` | Maximum cards added or returned in one call (clamped to 1–100). |
| `api_key` | empty | Optional AnkiConnect API key. Read through Hermes's secret scope from `ANKI_BRIDGE_API_KEY`; never stored in `config.yaml`. |

If AnkiConnect has an API key configured, set `ANKI_BRIDGE_API_KEY` in the
environment that launches Hermes (or in Hermes's own secret store). Leave it
unset for the default local setup.

## Safety and disclosures

- **Local network only by default.** The plugin POSTs JSON to the configured
  `anki_url`, which defaults to loopback. It never contacts a Hermes server or
  any analytics endpoint.
- **It changes your collection.** `anki_add_cards` creates notes and decks;
  `anki_answer` schedules the card immediately. Nothing is changed unless a
  tool is called.
- **No telemetry.** The plugin has no analytics, no update checker, and no
  background jobs.
- **No credential scraping.** It does not read `.env`, `auth.json`, browser
  storage, or Hermes config for secrets. The optional API key comes from
  Hermes's secret scope.
- **Duplicates are reported, not forced.** New notes use
  `allowDuplicate: false`; Anki's duplicate check can still be configured in
  Anki itself.
- **Remote endpoints are your call.** If you point `anki_url` at another
  machine, that machine receives your card text. AnkiConnect is normally bound
  to localhost; exposing it remotely is outside this plugin's control.
- **Back up first if you are experimenting.** Anki's own backup and export
  features are the right undo mechanism for collection changes.

## Development

```bash
python -m pytest -q
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
```

The tests use a fake AnkiConnect server on `127.0.0.1:0`; no real Anki install
is needed for CI.

## License

MIT
