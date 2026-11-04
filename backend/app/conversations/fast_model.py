"""Helpers for the prompts of the small jobs AFTER an answer (title, suggestions). The
traced call itself is `app.providers.llm.traced.complete_json_traced`."""

import html


def as_data(text: str) -> str:
    """Text placed inside a prompt tag: `<` and `>` escaped, so it cannot close the tag
    it is in or open another one."""
    return html.escape(text, quote=False)


def one_line(text: str) -> str:
    return " ".join(text.split())
