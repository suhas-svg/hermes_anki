"""Behaviour tests for the Anki bridge plugin.

The fake AnkiConnect server binds an ephemeral loopback port, so these tests
exercise the real HTTP client and JSON shapes without a running Anki install.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _load_plugin():
    """Load this directory as a package so the plugin's relative imports work."""
    name = "anki_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PLUGIN = _load_plugin()


class FakeCtx:
    """Records the registration calls a real Hermes plugin context would receive."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = dict(config or {})
        self.tools: dict[str, dict] = {}
        self.commands: dict[str, dict] = {}
        self.cli: dict[str, dict] = {}
        self.skills: dict[str, Path] = {}

    def get_config(self, key: str, default=None):
        return self.config.get(key, default)

    def register_tool(self, **kwargs) -> None:
        self.tools[kwargs["name"]] = kwargs

    def register_command(self, name: str, **kwargs) -> None:
        self.commands[name] = kwargs

    def register_cli_command(self, **kwargs) -> None:
        self.cli[kwargs["name"]] = kwargs

    def register_skill(self, name: str, path: Path) -> None:
        self.skills[name] = path


def _find_cards(query: str) -> list[int]:
    if "-is:new" in query:
        return [1] if "is:due" in query else []
    if "is:new" in query:
        return [2]
    if "is:due" in query:
        return [1, 2]
    return []


def _card(card_id: int) -> dict:
    return {
        "cardId": card_id,
        "deckName": "Spanish" if card_id == 1 else "Default",
        "modelName": "Basic",
        "fields": {
            "Front": {"value": f"Question {card_id}", "order": 0},
            "Back": {"value": f"Answer <b>{card_id}</b>", "order": 1},
        },
        "question": f"Question {card_id}",
        "answer": f"Answer <b>{card_id}</b>",
        "type": 2,
        "due": card_id,
        "interval": 3,
        "reps": 5,
        "lapses": 0,
    }


def _note(note_id: int) -> dict:
    return {
        "noteId": note_id,
        "modelName": "Basic",
        "fields": {
            "Front": {"value": f"Note {note_id} <b>bold</b>", "order": 0},
            "Back": {"value": f"Back {note_id}", "order": 1},
        },
        "tags": ["verbs"],
        "cards": [note_id + 100],
    }


class FakeAnki:
    """A tiny AnkiConnect double with per-action responses and errors."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.errors: dict[str, str] = {}
        self.responses = {
            "deckNames": ["Default"],
            "modelFieldNames": ["Front", "Back"],
            "createDeck": 1,
            "addNotes": [101, None],
            "addNote": 101,
            "findCards": lambda params: _find_cards(params["query"]),
            "cardsInfo": lambda params: [_card(card_id) for card_id in params["cards"]],
            "findNotes": [11, 12],
            "notesInfo": lambda params: [_note(note_id) for note_id in params["notes"]],
            "answerCards": [True],
            "getNumCardsReviewedToday": 7,
        }
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib method name
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                outer.requests.append(payload)
                action = payload.get("action")
                error = outer.errors.get(action)
                result = None if error else outer.responses.get(action)
                if callable(result):
                    result = result(payload.get("params", {}))
                body = json.dumps({"result": result, "error": error}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01),
            name="fake-anki",
            daemon=True,
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "fake Anki server did not stop"


@pytest.fixture
def fake_anki():
    fake = FakeAnki()
    try:
        yield fake
    finally:
        fake.stop()


def _tools(fake: FakeAnki, **config):
    values = {"anki_url": fake.url, "request_timeout": 2.0, **config}
    ctx = FakeCtx(values)
    return PLUGIN.AnkiTools(ctx, secret_getter=lambda _name: values.get("api_key_value", ""))


# ---------- registration ----------


def test_register_exposes_the_tools_command_cli_and_skill():
    ctx = FakeCtx()
    PLUGIN.register(ctx)

    expected = {"anki_decks", "anki_add_cards", "anki_search", "anki_due", "anki_answer"}
    assert set(ctx.tools) == expected
    for registered in ctx.tools.values():
        assert registered["toolset"] == "anki"
        assert registered["schema"]["description"]
        assert registered["schema"]["parameters"]["type"] == "object"
        assert callable(registered["handler"])
    assert "anki" in ctx.commands
    assert "anki" in ctx.cli
    assert ctx.skills["study"].name == "SKILL.md"
    assert ctx.skills["study"].is_file()


# ---------- status ----------


def test_decks_reports_disjoint_due_and_new_counts(fake_anki):
    payload = json.loads(_tools(fake_anki).decks({}))

    assert payload["success"] is True
    assert payload["connected"] is True
    assert payload["decks"] == ["Default"]
    assert payload["due"] == 1
    assert payload["new"] == 1
    assert payload["reviewed_today"] == 7
    queries = [r["params"]["query"] for r in fake_anki.requests if r["action"] == "findCards"]
    assert "is:due -is:new" in queries
    assert "is:new" in queries


# ---------- add ----------


def test_add_cards_creates_the_deck_and_reports_duplicates(fake_anki):
    fake_anki.responses["addNotes"] = [101, None]
    payload = json.loads(_tools(fake_anki).add_cards({
        "deck": "Spanish",
        "tags": ["verbs"],
        "cards": [
            {"front": "hablar", "back": "to speak"},
            {"front": "comer", "back": "to eat"},
        ],
    }))

    assert payload["success"] is True
    assert payload["added"] == 1
    assert payload["duplicates"] == 1
    assert payload["note_ids"] == [101]
    actions = [r["action"] for r in fake_anki.requests]
    assert "createDeck" in actions
    add = next(r for r in fake_anki.requests if r["action"] == "addNotes")
    notes = add["params"]["notes"]
    assert notes[0]["fields"] == {"Front": "hablar", "Back": "to speak"}
    assert notes[0]["tags"] == ["verbs"]
    assert notes[0]["options"]["allowDuplicate"] is False


def test_add_cards_does_not_recreate_an_existing_deck(fake_anki):
    fake_anki.responses["deckNames"] = ["Default", "Spanish"]
    _tools(fake_anki).add_cards({
        "deck": "Spanish",
        "cards": [{"front": "hola", "back": "hello"}],
    })
    assert "createDeck" not in [r["action"] for r in fake_anki.requests]


def test_add_cards_moves_cards_into_the_requested_deck(fake_anki):
    payload = json.loads(_tools(fake_anki).add_cards({
        "deck": "Spanish",
        "cards": [{"front": "hola", "back": "hello"}],
    }))

    assert payload["success"] is True
    assert payload["deck_fixed"] == 1
    move = next(r for r in fake_anki.requests if r["action"] == "changeDeck")
    assert move["params"] == {"cards": [201], "deck": "Spanish"}


def test_add_cards_leaves_cards_already_in_the_requested_deck_alone(fake_anki):
    fake_anki.responses["cardsInfo"] = lambda params: [
        {**_card(card_id), "deckName": "Spanish"} for card_id in params["cards"]
    ]
    payload = json.loads(_tools(fake_anki).add_cards({
        "deck": "Spanish",
        "cards": [{"front": "hola", "back": "hello"}],
    }))

    assert "deck_fixed" not in payload
    assert "changeDeck" not in [r["action"] for r in fake_anki.requests]


def test_add_cards_reports_a_deck_check_failure_without_failing_the_add(fake_anki):
    fake_anki.errors["cardsInfo"] = "cardsInfo unavailable"
    payload = json.loads(_tools(fake_anki).add_cards({
        "deck": "Spanish",
        "cards": [{"front": "hola", "back": "hello"}],
    }))

    assert payload["success"] is True
    assert payload["added"] == 1
    assert "could not be verified" in payload["warning"]


def test_add_cards_skips_the_deck_check_when_every_note_is_a_duplicate(fake_anki):
    fake_anki.responses["addNotes"] = [None]
    _tools(fake_anki).add_cards({
        "deck": "Spanish",
        "cards": [{"front": "hola", "back": "hello"}],
    })
    assert "notesInfo" not in [r["action"] for r in fake_anki.requests]


def test_add_cards_rejects_unknown_fields_before_writing(fake_anki):
    payload = json.loads(_tools(fake_anki).add_cards({
        "notes": [{"fields": {"Prompt": "hola"}}],
    }))

    assert "Unknown field" in payload["error"]
    assert "Front" in payload["error"]
    assert "addNotes" not in [r["action"] for r in fake_anki.requests]


def test_add_cards_rejects_tags_with_spaces(fake_anki):
    payload = json.loads(_tools(fake_anki).add_cards({
        "cards": [{"front": "hola", "back": "hello"}],
        "tags": ["two words"],
    }))
    assert "whitespace" in payload["error"]
    assert fake_anki.requests == []


# ---------- due / quiz ----------


def test_due_returns_plain_text_and_uses_deck_queries(fake_anki):
    payload = json.loads(_tools(fake_anki).due({"deck": "Spanish"}))

    assert payload["count"] == 2
    assert payload["cards"][0]["card_id"] == 1
    assert payload["cards"][0]["front"] == "Question 1"
    assert payload["cards"][0]["back"] == "Answer 1"
    queries = [r["params"]["query"] for r in fake_anki.requests if r["action"] == "findCards"]
    assert 'deck:"Spanish" is:due' in queries
    assert 'deck:"Spanish" is:new' in queries


def test_due_with_no_cards_explains_the_next_step(fake_anki):
    fake_anki.responses["findCards"] = lambda _params: []
    payload = json.loads(_tools(fake_anki).due({}))
    assert payload["count"] == 0
    assert "No cards are due" in payload["message"]


def test_answer_maps_good_to_ease_three_and_reports_the_next_interval(fake_anki):
    payload = json.loads(_tools(fake_anki).answer({"card_id": 1, "ease": "good"}))

    assert payload["answered"] is True
    assert payload["ease"] == 3
    assert payload["ease_name"] == "good"
    assert payload["next_interval"] == 3
    answer = next(r for r in fake_anki.requests if r["action"] == "answerCards")
    assert answer["params"]["answers"] == [{"cardId": 1, "ease": 3}]


def test_answer_rejects_an_unknown_grade_without_calling_anki(fake_anki):
    payload = json.loads(_tools(fake_anki).answer({"card_id": 1, "ease": "perfect"}))
    assert "ease must be" in payload["error"]
    assert "answerCards" not in [r["action"] for r in fake_anki.requests]


# ---------- search ----------


def test_search_returns_plain_text_fields_and_card_ids(fake_anki):
    payload = json.loads(_tools(fake_anki).search({"query": "tag:verbs", "limit": 2}))

    assert payload["count"] == 2
    assert payload["notes"][0]["note_id"] == 11
    assert payload["notes"][0]["fields"]["Front"] == "Note 11 bold"
    assert payload["notes"][0]["card_ids"] == [111]


def test_search_requires_a_query(fake_anki):
    payload = json.loads(_tools(fake_anki).search({"query": "  "}))
    assert "non-empty" in payload["error"]
    assert fake_anki.requests == []


# ---------- client resilience ----------


def test_connection_error_is_actionable_and_never_raises():
    fake = FakeAnki()
    url = fake.url
    fake.stop()
    ctx = FakeCtx({"anki_url": url, "request_timeout": 1.0})
    payload = json.loads(PLUGIN.AnkiTools(ctx, secret_getter=lambda _name: "").decks({}))

    assert "Could not reach AnkiConnect" in payload["error"]
    assert "Anki desktop" in payload["hint"]


def test_api_key_is_sent_only_when_configured(fake_anki):
    _tools(fake_anki, api_key_value="top-secret").decks({})
    assert all(request.get("key") == "top-secret" for request in fake_anki.requests)

    fake_anki.requests.clear()
    _tools(fake_anki).decks({})
    assert all("key" not in request for request in fake_anki.requests)


def test_add_notes_falls_back_to_the_single_note_action(fake_anki):
    fake_anki.errors["addNotes"] = "unsupported action"
    client = PLUGIN.client.AnkiClient(PLUGIN.client.AnkiConfig(url=fake_anki.url, timeout=2.0))
    result = client.add_notes([{
        "deckName": "Default",
        "modelName": "Basic",
        "fields": {"Front": "a", "Back": "b"},
        "tags": [],
    }])

    assert result == [101]
    assert [r["action"] for r in fake_anki.requests] == ["addNotes", "addNote"]


def test_invalid_url_is_reported_as_a_tool_error():
    ctx = FakeCtx({"anki_url": "ftp://127.0.0.1:8765"})
    payload = json.loads(PLUGIN.AnkiTools(ctx, secret_getter=lambda _name: "").decks({}))
    assert "must start with http" in payload["error"]


# ---------- slash + CLI ----------


def test_slash_status_and_due_are_human_readable(fake_anki):
    tools = _tools(fake_anki)
    status = tools.slash("status")
    assert "AnkiConnect is connected" in status
    assert "Due now: 1 scheduled, 1 new" in status
    assert "Reviewed today: 7" in status

    due = tools.slash("due Spanish")
    assert "Due in Spanish" in due
    assert "Question 1" in due
    assert "Answer" not in due


def test_slash_help_and_unknown_command(fake_anki):
    tools = _tools(fake_anki)
    assert "Usage: /anki" in tools.slash("help")
    assert "Unknown /anki subcommand" in tools.slash("nope")


def test_cli_status_and_search(fake_anki, capsys):
    tools = _tools(fake_anki)
    parser = argparse.ArgumentParser()
    PLUGIN._setup_cli(parser)

    assert tools.cli(parser.parse_args(["status"])) == 0
    out = capsys.readouterr().out
    assert "AnkiConnect:" in out
    assert "Due: 1 scheduled, 1 new" in out

    assert tools.cli(parser.parse_args(["search", "tag:verbs"])) == 0
    out = capsys.readouterr().out
    assert "Note 11 bold" in out


def test_strip_html_keeps_readable_line_breaks():
    assert PLUGIN.client.strip_html("<b>hola</b><br>mundo") == "hola\nmundo"
