"""Tool, slash-command, and CLI handlers for the Anki bridge."""

from __future__ import annotations

import json
import logging
import shlex
from functools import wraps
from typing import Any, Callable, Iterable

from .client import (
    ANKICONNECT_ADDON_CODE,
    DEFAULT_URL,
    AnkiClient,
    AnkiConfig,
    AnkiError,
    strip_html,
)

logger = logging.getLogger(__name__)

_MAX_LIST = 50
_EASE_VALUES = {"again": 1, "hard": 2, "good": 3, "easy": 4}
_TYPE_LABELS = {0: "new", 1: "learning", 2: "review", 3: "relearning"}
_SECRET_ENV = "ANKI_BRIDGE_API_KEY"


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(message: str, *, hint: str = "") -> str:
    payload = {"error": message}
    if hint:
        payload["hint"] = hint
    return json.dumps(payload, ensure_ascii=False)


def _guard(fn: Callable[..., str]) -> Callable[..., str]:
    @wraps(fn)
    def wrapper(self: "AnkiTools", args: dict[str, Any], **kwargs: Any) -> str:
        try:
            return fn(self, args or {}, **kwargs)
        except AnkiError as exc:
            return _error(str(exc), hint=exc.hint)
        except Exception as exc:  # tool handlers must never raise
            logger.exception("anki tool %s failed", fn.__name__)
            return _error(f"Unexpected Anki plugin error: {exc}")

    return wrapper


def _clamp_int(raw: Any, default: int, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _positive_int(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AnkiError("card_id must be an integer from anki_due.") from exc
    if value <= 0:
        raise AnkiError("card_id must be a positive Anki card id.")
    return value


def _as_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool(raw)


def _tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    values: Iterable[Any] = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, (list, tuple)):
        raise AnkiError("tags must be a list of strings.")
    result: list[str] = []
    for item in values:
        tag = str(item).strip()
        if not tag:
            continue
        if any(char.isspace() for char in tag):
            raise AnkiError(
                f"Tag {tag!r} contains whitespace. Anki tags are single tokens; "
                "use hyphens or underscores."
            )
        if tag not in result:
            result.append(tag)
    return result


def _hermes_secret(name: str) -> str:
    """Read a secret through Hermes's profile-aware secret scope."""
    try:
        from agent.secret_scope import get_secret
    except Exception:
        return ""
    try:
        return str(get_secret(name) or "")
    except Exception:
        logger.debug("secret lookup failed for %s", name, exc_info=True)
        return ""


class AnkiTools:
    """State-free handlers bound to a plugin context."""

    def __init__(self, ctx: Any, secret_getter: Callable[[str], str] | None = None) -> None:
        self.ctx = ctx
        self._secret_getter = secret_getter or _hermes_secret

    # ---------- config / client ----------

    def _get(self, key: str, default: Any) -> Any:
        try:
            value = self.ctx.get_config(key, default)
        except Exception:
            logger.debug("get_config failed for %s", key, exc_info=True)
            return default
        return default if value is None else value

    def _config(self) -> AnkiConfig:
        url = str(self._get("anki_url", DEFAULT_URL)).strip() or DEFAULT_URL
        if not url.startswith(("http://", "https://")):
            raise AnkiError(
                "anki_url must start with http:// or https://.",
                hint="The default AnkiConnect endpoint is http://127.0.0.1:8765.",
            )
        try:
            timeout = float(self._get("request_timeout", 10.0) or 10.0)
        except (TypeError, ValueError) as exc:
            raise AnkiError(
                "request_timeout must be a number of seconds.",
                hint="Set plugins.entries.anki.settings.request_timeout to a number such as 10.",
            ) from exc
        timeout = max(1.0, min(120.0, timeout))
        return AnkiConfig(
            url=url,
            timeout=timeout,
            api_key=self._secret_getter(_SECRET_ENV) or "",
        )

    def _client(self) -> AnkiClient:
        return AnkiClient(self._config())

    def _max_cards(self) -> int:
        return _clamp_int(self._get("max_cards", 50), 50, 1, 100)

    # ---------- model-callable tools ----------

    @_guard
    def decks(self, args: dict[str, Any]) -> str:
        client = self._client()
        status = client.status()
        return _ok({"success": True, **status})

    @_guard
    def add_cards(self, args: dict[str, Any]) -> str:
        deck = str(args.get("deck") or self._get("default_deck", "Default")).strip()
        model = str(args.get("model") or self._get("default_model", "Basic")).strip()
        if not deck:
            raise AnkiError("deck must be a non-empty name.")
        if not model:
            raise AnkiError("model must be a non-empty name.")

        cards = args.get("cards")
        notes = args.get("notes")
        if bool(cards) == bool(notes):
            raise AnkiError("Pass exactly one of `cards` or `notes`.")
        if not isinstance(cards or notes, list):
            raise AnkiError("`cards` and `notes` must be arrays.")

        common_tags = _tags(args.get("tags"))
        limit = self._max_cards()
        items = cards or notes or []
        if len(items) > limit:
            raise AnkiError(f"Too many notes in one call ({len(items)}); limit is {limit}.")

        client = self._client()
        field_names = client.model_field_names(model)
        prepared: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise AnkiError(f"Entry {index} must be an object.")
            item_tags = _tags(item.get("tags"))
            tags = list(dict.fromkeys(common_tags + item_tags))
            if cards:
                front = str(item.get("front") or "").strip()
                back = str(item.get("back") or "").strip()
                if not front or not back:
                    raise AnkiError(f"Card {index} needs non-empty front and back fields.")
                if len(field_names) < 2:
                    raise AnkiError(
                        f"Model `{model}` has fewer than two fields; pass `notes` "
                        "with explicit field names instead."
                    )
                fields = {field_names[0]: front, field_names[1]: back}
            else:
                raw_fields = item.get("fields")
                if not isinstance(raw_fields, dict) or not raw_fields:
                    raise AnkiError(f"Note {index} needs a non-empty `fields` object.")
                unknown = [name for name in raw_fields if name not in field_names]
                if unknown:
                    raise AnkiError(
                        f"Unknown field(s) for model `{model}`: {', '.join(unknown)}. "
                        f"Valid fields: {', '.join(field_names)}."
                    )
                fields = {name: str(value or "") for name, value in raw_fields.items()}
            prepared.append({
                "deckName": deck,
                "modelName": model,
                "fields": fields,
                "tags": tags,
                "options": {"allowDuplicate": False},
            })

        client.ensure_deck(deck)
        note_ids = client.add_notes(prepared)
        added_ids = [note_id for note_id in note_ids if isinstance(note_id, int)]
        return _ok({
            "success": True,
            "deck": deck,
            "model": model,
            "requested": len(prepared),
            "added": len(added_ids),
            "duplicates": len(note_ids) - len(added_ids),
            "note_ids": added_ids,
        })

    @_guard
    def search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            raise AnkiError("query must be a non-empty Anki search.")
        limit = _clamp_int(args.get("limit", 20), 20, 1, _MAX_LIST)
        notes = self._client().search_notes(query, limit=limit)
        rows = []
        for note in notes:
            fields = note.get("fields") or {}
            rows.append({
                "note_id": note.get("noteId"),
                "model": note.get("modelName"),
                "fields": {
                    str(name): strip_html(value.get("value") if isinstance(value, dict) else value)
                    for name, value in fields.items()
                },
                "tags": note.get("tags") or [],
                "card_ids": note.get("cards") or [],
            })
        return _ok({"success": True, "query": query, "count": len(rows), "notes": rows})

    @_guard
    def due(self, args: dict[str, Any]) -> str:
        deck = str(args.get("deck") or "").strip()
        include_new = _as_bool(args.get("include_new"), True)
        limit = _clamp_int(args.get("limit", 20), 20, 1, _MAX_LIST)
        cards = self._client().due_cards(deck, include_new=include_new, limit=limit)
        rows = [_card_row(card) for card in cards]
        payload: dict[str, Any] = {
            "success": True,
            "deck": deck or "all",
            "count": len(rows),
            "cards": rows,
        }
        if not rows:
            payload["message"] = (
                "No cards are due. Try `anki_decks` to confirm the deck and "
                "`anki_search` to find cards, or add a few with `anki_add_cards`."
            )
        return _ok(payload)

    @_guard
    def answer(self, args: dict[str, Any]) -> str:
        card_id = _positive_int(args.get("card_id"))
        raw_ease = args.get("ease")
        if isinstance(raw_ease, int) and not isinstance(raw_ease, bool):
            ease = raw_ease
        else:
            ease = _EASE_VALUES.get(str(raw_ease or "").strip().lower(), 0)
        if ease not in (1, 2, 3, 4):
            raise AnkiError(
                "ease must be again, hard, good, or easy (or 1-4).",
            )

        client = self._client()
        accepted = client.answer_cards([{"cardId": card_id, "ease": ease}])
        if not accepted or accepted[0] is not True:
            raise AnkiError(
                f"Anki did not accept the grade for card {card_id}.",
                hint="Refresh the card with anki_due and try again; the card may have been reviewed elsewhere.",
            )
        info = client.cards_info([card_id])
        detail = info[0] if info else {}
        return _ok({
            "success": True,
            "answered": True,
            "card_id": card_id,
            "ease": ease,
            "ease_name": next((name for name, value in _EASE_VALUES.items() if value == ease), str(ease)),
            "next_interval": detail.get("interval"),
            "next_due": detail.get("due"),
            "reps": detail.get("reps"),
            "lapses": detail.get("lapses"),
        })

    # ---------- /anki slash command ----------

    def slash(self, raw_args: str, **kwargs: Any) -> str:
        del kwargs
        try:
            parts = shlex.split(raw_args or "")
        except ValueError as exc:
            return f"Could not parse /anki arguments: {exc}\n\n{_slash_help()}"
        command = parts[0].lower() if parts else "status"
        if command in {"help", "-h", "--help"}:
            return _slash_help()
        try:
            if command == "status":
                return self._slash_status()
            if command == "due":
                return self._slash_due(" ".join(parts[1:]).strip())
            return f"Unknown /anki subcommand: {command}\n\n{_slash_help()}"
        except AnkiError as exc:
            hint = f"\nHint: {exc.hint}" if exc.hint else ""
            return f"Anki error: {exc}{hint}"
        except Exception as exc:  # a slash handler must return text, not crash the turn
            logger.exception("/anki failed")
            return f"Unexpected Anki error: {exc}"

    def _slash_status(self) -> str:
        status = self._client().status()
        decks = status.get("decks") or []
        reviewed = status.get("reviewed_today")
        reviewed_text = "unknown" if reviewed is None else str(reviewed)
        deck_text = ", ".join(decks[:12]) if decks else "(none)"
        more = f" (+{len(decks) - 12} more)" if len(decks) > 12 else ""
        return (
            f"AnkiConnect is connected at {status['url']}.\n"
            f"Decks: {deck_text}{more}\n"
            f"Due now: {status['due']} scheduled, {status['new']} new. "
            f"Reviewed today: {reviewed_text}.\n"
            "Try `/anki due [deck]` or ask Hermes to quiz you."
        )

    def _slash_due(self, deck: str) -> str:
        limit = min(5, self._max_cards())
        cards = [card for card in self._client().due_cards(deck, limit=limit)]
        if not cards:
            where = f" in {deck}" if deck else ""
            return f"No cards are due{where}. Add a few with Hermes, or come back later."
        lines = [f"Due{' in ' + deck if deck else ''} ({len(cards)} shown):"]
        for index, card in enumerate(cards, start=1):
            row = _card_row(card)
            lines.append(f"{index}. {row['front']}  [card {row['card_id']}]")
        lines.append("Ask Hermes to quiz you; it will show one card at a time and grade your answer.")
        return "\n".join(lines)

    # ---------- hermes anki CLI ----------

    def cli(self, args: Any) -> int:
        command = getattr(args, "anki_command", None) or "status"
        try:
            if command == "status":
                status = self._client().status()
                print(f"AnkiConnect: {status['url']} (connected)")
                print(f"Decks: {len(status['decks'])}")
                print(f"Due: {status['due']} scheduled, {status['new']} new")
                print(f"Reviewed today: {status['reviewed_today'] if status['reviewed_today'] is not None else 'unknown'}")
                return 0
            if command == "decks":
                for name in self._client().deck_names():
                    print(name)
                return 0
            if command == "search":
                query = " ".join(getattr(args, "query", []) or [])
                notes = self._client().search_notes(query, limit=10)
                for note in notes:
                    fields = note.get("fields") or {}
                    first = next(iter(fields.values()), {})
                    value = first.get("value") if isinstance(first, dict) else first
                    print(f"{note.get('noteId')}: {strip_html(value)}")
                return 0
        except AnkiError as exc:
            print(f"Anki error: {exc}")
            if exc.hint:
                print(f"Hint: {exc.hint}")
            return 1
        except Exception as exc:  # a CLI command must not traceback on bad config
            logger.exception("hermes anki failed")
            print(f"Unexpected Anki error: {exc}")
            return 1
        print("Usage: hermes anki <status|decks|search QUERY>")
        return 2


def _setup_cli(subparser: Any) -> None:
    subs = subparser.add_subparsers(dest="anki_command")
    subs.add_parser("status", help="Check AnkiConnect and show due counts")
    subs.add_parser("decks", help="List deck names")
    search = subs.add_parser("search", help="Search notes with Anki's search syntax")
    search.add_argument("query", nargs="+", help="Anki search query")


def _card_row(card: dict[str, Any]) -> dict[str, Any]:
    fields = card.get("fields") or {}
    front = card.get("question")
    back = card.get("answer")
    if not front:
        first = next(iter(fields.values()), {})
        front = first.get("value") if isinstance(first, dict) else first
    if not back:
        values = list(fields.values())
        second = values[1] if len(values) > 1 else {}
        back = second.get("value") if isinstance(second, dict) else second
    return {
        "card_id": card.get("cardId"),
        "deck": card.get("deckName"),
        "model": card.get("modelName"),
        "front": strip_html(front),
        "back": strip_html(back),
        "type": _TYPE_LABELS.get(card.get("type"), card.get("type")),
        "due": card.get("due"),
        "interval": card.get("interval"),
        "reps": card.get("reps"),
        "lapses": card.get("lapses"),
    }


def _slash_help() -> str:
    return (
        "Usage: /anki [status|due [deck]]\n"
        "  status       Check AnkiConnect, deck count, and due cards.\n"
        "  due [deck]   Show up to five due cards (front only).\n"
        f"Requires Anki desktop with AnkiConnect (code {ANKICONNECT_ADDON_CODE}) running."
    )
