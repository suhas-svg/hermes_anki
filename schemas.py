"""JSON Schemas for the five Anki tools."""

from __future__ import annotations

from typing import Any

DECKS_SCHEMA: dict[str, Any] = {
    "description": (
        "Check that Anki is reachable and show the collection's decks, scheduled "
        "due count, new-card count, and reviews completed today. Use this first "
        "when the user asks about Anki."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

ADD_CARDS_SCHEMA: dict[str, Any] = {
    "description": (
        "Create Anki notes in a deck. Pass `cards` for Basic-style front/back "
        "cards, or `notes` with an explicit Anki `fields` object for other note "
        "models. The deck is created if it does not exist. Duplicate cards are "
        "reported, not forced. Always show the proposed cards to the user before "
        "calling this unless they already asked you to add them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "deck": {
                "type": "string",
                "description": "Target deck. Defaults to the plugin's default_deck setting.",
            },
            "model": {
                "type": "string",
                "description": "Anki note model. Defaults to the plugin's default_model setting (usually Basic).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags applied to every note in this call.",
            },
            "cards": {
                "type": "array",
                "description": "Front/back cards for a two-field model such as Basic.",
                "items": {
                    "type": "object",
                    "properties": {
                        "front": {"type": "string"},
                        "back": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["front", "back"],
                    "additionalProperties": False,
                },
            },
            "notes": {
                "type": "array",
                "description": "Advanced notes with explicit Anki field names. Use when the model is not a two-field front/back model.",
                "items": {
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "object",
                            "description": "Mapping of Anki field name to field value.",
                            "additionalProperties": {"type": "string"},
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["fields"],
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    },
}

SEARCH_SCHEMA: dict[str, Any] = {
    "description": (
        "Search Anki notes with Anki's own search syntax (for example "
        "`deck:Spanish tag:verbs`, `is:due`, or `added:7`). Returns note fields "
        "as plain text. Use this to find existing cards before creating duplicates."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Anki search query.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum notes to return (1-50). Defaults to 20.",
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

DUE_SCHEMA: dict[str, Any] = {
    "description": (
        "Return the cards that are ready to review, with the front and back as "
        "plain text. For a quiz, show only the front, wait for the user's answer, "
        "then call `anki_answer`; do not reveal the back before they answer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "deck": {
                "type": "string",
                "description": "Limit to one deck. Omit to review across the whole collection.",
            },
            "include_new": {
                "type": "boolean",
                "description": "Include new cards as well as scheduled due cards. Defaults to true.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum cards to return (1-50). Defaults to 20.",
                "minimum": 1,
                "maximum": 50,
            },
        },
        "additionalProperties": False,
    },
}

ANSWER_SCHEMA: dict[str, Any] = {
    "description": (
        "Grade a reviewed card and schedule its next appearance. Use after the "
        "user answers a card returned by `anki_due`: map their self-grade to "
        "again, hard, good, or easy. This changes the card's scheduling in Anki."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "card_id": {
                "type": "integer",
                "description": "Anki card id from `anki_due`.",
            },
            "ease": {
                "type": "string",
                "enum": ["again", "hard", "good", "easy"],
                "description": "The user's grade. `again` resets the card; `easy` lengthens the interval most.",
            },
        },
        "required": ["card_id", "ease"],
        "additionalProperties": False,
    },
}
