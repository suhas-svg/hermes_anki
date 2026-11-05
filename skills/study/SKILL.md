---
name: anki-study
description: "Build atomic Anki cards from notes and quiz due cards through the anki plugin."
version: 1.0.0
author: suhas-svg
license: MIT
metadata:
  hermes:
    tags: [anki, flashcards, spaced-repetition, study]
---

# Anki study workflow

Use this skill when the user wants to learn, memorize, review, or be quizzed on
material with Anki, or asks to turn notes into flashcards.

## Create cards

1. Call `anki_decks` first when the collection or deck is unknown. It confirms
   AnkiConnect is reachable and shows the decks, due counts, and reviews today.
2. Draft 3–7 atomic cards. One fact per card, a short prompt on the front, and
   just enough context on the back. Split lists and multi-step facts instead of
   writing an essay on one card.
3. Show the proposed cards to the user and ask which deck to use unless they
   already named one. Do not write cards the user has not seen.
4. Call `anki_add_cards` with `cards` for a two-field model such as Basic, or
   with `notes` and explicit field names for another model.
5. Report what was added, including any duplicates Anki refused. Use
   `anki_search` first when the user asks to avoid duplicate cards.

Good card:

- Front: `¿Qué significa "aprovechar"?`
- Back: `To take advantage of / make the most of.`

Bad card:

- Front: `Tell me everything about Spanish verbs.`
- Back: a paragraph covering conjugation, irregulars, and usage.

## Quiz the user

1. Call `anki_due` with the deck if the user named one.
2. Show only the `front`. Wait for the user's answer. Never reveal the `back`
   before they answer.
3. Grade their answer with the closest ease:
   - missed it or asked for the answer → `again`
   - got it with effort → `hard`
   - got it → `good`
   - instant and confident → `easy`
4. Call `anki_answer` with the card id and ease. Report the next interval and
   move to the next card only if the user wants to continue.
5. If nothing is due, say so and offer to add cards or search existing ones.

## Pitfalls

- If a tool returns an error saying AnkiConnect is unreachable, tell the user to
  open Anki desktop and confirm the AnkiConnect add-on (code `2055492159`) is
  installed, then run `hermes anki status`. Do not retry the same call in a loop.
- `anki_answer` changes the card's schedule immediately. Only call it after the
  user has actually answered and agreed to the grade.
- Tags are single tokens. Use hyphens or underscores, not spaces.
- Anki may refuse a duplicate. That is not a failure; report it and move on.
