"""AnkiConnect HTTP client -- stdlib only, no network beyond the configured endpoint."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import unescape
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8765"
ANKICONNECT_ADDON_CODE = "2055492159"
_MAX_ERROR_BODY = 500


class AnkiError(RuntimeError):
    """A user-actionable AnkiConnect failure."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class AnkiConfig:
    url: str = DEFAULT_URL
    timeout: float = 10.0
    api_key: str = ""


def strip_html(value: Any) -> str:
    """Turn an Anki field (HTML) into readable plain text."""
    text = str(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:div|p|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _known_action_hint(action: str) -> str:
    return (
        f"AnkiConnect did not recognise `{action}`. Update the AnkiConnect add-on "
        f"(code {ANKICONNECT_ADDON_CODE}) from Anki's Tools -> Add-ons dialog."
    )


class AnkiClient:
    """Small, defensive wrapper around AnkiConnect's JSON API."""

    def __init__(self, config: AnkiConfig) -> None:
        self.config = config
        self.url = config.url.rstrip("/")
        # AnkiConnect is local by default; never send loopback calls through an
        # HTTP_PROXY inherited from the user's shell.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, action: str, **params: Any) -> Any:
        payload: dict[str, Any] = {"action": action, "version": 6, "params": params}
        if self.config.api_key:
            payload["key"] = self.config.api_key
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.config.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:_MAX_ERROR_BODY]
            raise AnkiError(
                f"AnkiConnect returned HTTP {exc.code} for `{action}`: {body}",
                hint=f"Check the AnkiConnect log and the endpoint {self.url}.",
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AnkiError(
                f"Could not reach AnkiConnect at {self.url}: {exc}",
                hint=(
                    "Open the Anki desktop app and confirm the AnkiConnect add-on "
                    f"(code {ANKICONNECT_ADDON_CODE}) is installed. Then run "
                    "`hermes anki status`."
                ),
            ) from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnkiError(
                f"AnkiConnect returned invalid JSON for `{action}`.",
                hint=f"Check that {self.url} is the AnkiConnect endpoint, not another service.",
            ) from exc
        if not isinstance(data, dict):
            raise AnkiError(f"AnkiConnect returned an unexpected response for `{action}`.")
        error = data.get("error")
        if error:
            message = str(error)
            hint = _known_action_hint(action) if _is_unknown_action(message) else ""
            raise AnkiError(message, hint=hint)
        return data.get("result")

    def status(self) -> dict[str, Any]:
        decks = self.deck_names()
        # Anki's `is:due` includes cards in the learning queue and, on some
        # versions, new cards. Subtract new cards so the two counts are
        # disjoint.
        due = len(self.find_cards("is:due -is:new"))
        new = len(self.find_cards("is:new"))
        try:
            reviewed = self.num_reviewed_today()
        except AnkiError:
            reviewed = None
        return {
            "connected": True,
            "url": self.url,
            "decks": decks,
            "due": due,
            "new": new,
            "reviewed_today": reviewed,
        }

    def deck_names(self) -> list[str]:
        result = self._request("deckNames")
        if not isinstance(result, list):
            raise AnkiError("AnkiConnect returned no deck list.")
        return [str(name) for name in result]

    def create_deck(self, name: str) -> None:
        self._request("createDeck", deck=name)

    def ensure_deck(self, name: str) -> None:
        if name not in self.deck_names():
            self.create_deck(name)

    def model_field_names(self, model: str) -> list[str]:
        result = self._request("modelFieldNames", modelName=model)
        if not isinstance(result, list) or not result:
            raise AnkiError(
                f"Anki note model `{model}` was not found or has no fields.",
                hint="Create the model in Anki, or pass a model that already exists.",
            )
        return [str(field) for field in result]

    def add_notes(self, notes: list[dict[str, Any]]) -> list[Any]:
        if not notes:
            return []
        try:
            result = self._request("addNotes", notes=notes)
        except AnkiError as exc:
            if not _is_unknown_action(str(exc)):
                raise
            # Older AnkiConnect builds only expose the single-note action.
            return [self._request("addNote", note=note) for note in notes]
        if not isinstance(result, list):
            raise AnkiError("AnkiConnect returned an unexpected addNotes result.")
        return result

    def find_cards(self, query: str) -> list[int]:
        result = self._request("findCards", query=query)
        if not isinstance(result, list):
            raise AnkiError("AnkiConnect returned no card list.")
        return [int(card_id) for card_id in result]

    def cards_info(self, card_ids: list[int]) -> list[dict[str, Any]]:
        if not card_ids:
            return []
        result = self._request("cardsInfo", cards=card_ids)
        if not isinstance(result, list):
            raise AnkiError("AnkiConnect returned no card details.")
        return [card for card in result if isinstance(card, dict)]

    def change_deck(self, card_ids: list[int], deck: str) -> None:
        if not card_ids:
            return
        self._request("changeDeck", cards=card_ids, deck=deck)

    def ensure_notes_in_deck(self, note_ids: list[int], deck: str) -> int:
        """Move cards belonging to *note_ids* into *deck*; return how many moved.

        Some AnkiConnect builds ignore ``deckName`` when adding notes (observed
        with an older build on Anki 26.09). ``changeDeck`` is the stable action
        across those versions, so verify the cards afterwards and repair any
        strays instead of trusting the add response.
        """
        notes = self.notes_info(note_ids)
        card_ids: list[int] = []
        for note in notes:
            for card_id in note.get("cards") or []:
                try:
                    card_ids.append(int(card_id))
                except (TypeError, ValueError):
                    continue
        if not card_ids:
            return 0
        cards = self.cards_info(card_ids)
        strays: list[int] = []
        for card in cards:
            card_id = card.get("cardId")
            if card.get("deckName") != deck and isinstance(card_id, int):
                strays.append(card_id)
        if strays:
            self.change_deck(strays, deck)
        return len(strays)

    def find_notes(self, query: str) -> list[int]:
        result = self._request("findNotes", query=query)
        if not isinstance(result, list):
            raise AnkiError("AnkiConnect returned no note list.")
        return [int(note_id) for note_id in result]

    def notes_info(self, note_ids: list[int]) -> list[dict[str, Any]]:
        if not note_ids:
            return []
        result = self._request("notesInfo", notes=note_ids)
        if not isinstance(result, list):
            raise AnkiError("AnkiConnect returned no note details.")
        return [note for note in result if isinstance(note, dict)]

    def answer_cards(self, cards: list[dict[str, int]]) -> list[Any]:
        result = self._request("answerCards", answers=cards)
        if not isinstance(result, list):
            raise AnkiError(
                "AnkiConnect returned an unexpected answerCards result.",
                hint="Update AnkiConnect if grading is unavailable.",
            )
        return result

    def num_reviewed_today(self) -> int:
        result = self._request("getNumCardsReviewedToday")
        if isinstance(result, dict):
            result = result.get("today", result.get("count", 0))
        return int(result or 0)

    def due_cards(
        self,
        deck: str = "",
        *,
        include_new: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        queries: list[str] = []
        if deck:
            deck_query = _deck_query(deck)
            queries.append(f"{deck_query} is:due")
            if include_new:
                queries.append(f"{deck_query} is:new")
        else:
            queries.append("is:due")
            if include_new:
                queries.append("is:new")

        card_ids: list[int] = []
        for query in queries:
            for card_id in self.find_cards(query):
                if card_id not in card_ids:
                    card_ids.append(card_id)
        return self.cards_info(card_ids[: max(1, limit)])

    def search_notes(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        note_ids = self.find_notes(query)[: max(1, limit)]
        return self.notes_info(note_ids)


def _deck_query(deck: str) -> str:
    escaped = deck.replace("\\", "\\\\").replace('"', '\\"')
    return f'deck:"{escaped}"'


def _is_unknown_action(message: str) -> bool:
    lowered = message.lower()
    return any(
        phrase in lowered
        for phrase in ("unsupported action", "unknown action", "action not found", "not supported")
    )
