"""Anki bridge plugin entry point."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .schemas import ADD_CARDS_SCHEMA, ANSWER_SCHEMA, DECKS_SCHEMA, DUE_SCHEMA, SEARCH_SCHEMA
from .tools import AnkiTools, _setup_cli

logger = logging.getLogger(__name__)

_TOOL_DESCRIPTIONS = {
    "anki_decks": "Check AnkiConnect and show decks plus due and new counts.",
    "anki_add_cards": "Create Anki cards or notes in a deck, creating the deck if needed.",
    "anki_search": "Search Anki notes with Anki's own search syntax.",
    "anki_due": "Return due cards as plain text, with the back separated from the front.",
    "anki_answer": "Grade a due card and schedule its next review.",
}


def register(ctx: Any) -> None:
    """Register the plugin's tools, slash command, CLI command, and skill."""
    tools = AnkiTools(ctx)
    for name, schema in (
        ("anki_decks", DECKS_SCHEMA),
        ("anki_add_cards", ADD_CARDS_SCHEMA),
        ("anki_search", SEARCH_SCHEMA),
        ("anki_due", DUE_SCHEMA),
        ("anki_answer", ANSWER_SCHEMA),
    ):
        ctx.register_tool(
            name=name,
            toolset="anki",
            schema=schema,
            handler=getattr(tools, name.removeprefix("anki_")),
            description=_TOOL_DESCRIPTIONS[name],
        )

    ctx.register_command(
        "anki",
        handler=tools.slash,
        description="Anki status and due cards",
        args_hint="[status|due [deck]]",
    )
    ctx.register_cli_command(
        name="anki",
        help="Manage the local Anki bridge",
        setup_fn=_setup_cli,
        handler_fn=tools.cli,
    )

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)

    logger.debug("anki: registered 5 tools, /anki, hermes anki, and bundled skills")
