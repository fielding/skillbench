"""Minimal reader for the YAML frontmatter skillbench needs.

Only a few scalar and list keys are ever read (a skill's ``name``, a case's
``allowed_tools`` and ``scaffold_script``), so a full YAML parser would be a
dependency for nothing. Anything this reader cannot understand is treated as
absent rather than guessed.
"""

from __future__ import annotations

import re

FENCE = "---"


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter, body)``; frontmatter is empty when the file has none."""
    if not text.startswith(FENCE):
        return "", text
    end = text.find("\n" + FENCE, len(FENCE))
    if end == -1:
        return "", text
    frontmatter = text[len(FENCE) : end].strip("\n")
    body = text[end + len(FENCE) + 1 :]
    return frontmatter, body.lstrip("\n")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def scalar(frontmatter: str, key: str) -> str | None:
    """Read a top-level (or nested, first match) scalar, including ``>-``/``|`` blocks."""
    match = re.search(rf"^[ \t]*{re.escape(key)}:[ \t]*(.*)$", frontmatter, re.M)
    if not match:
        return None
    value = match.group(1).strip()
    if value in (">", ">-", "|", "|-"):
        continuation: list[str] = []
        for line in frontmatter[match.end() :].split("\n")[1:]:
            if line.strip() and not line[0].isspace():
                break
            continuation.append(line.strip())
        joiner = "\n" if value.startswith("|") else " "
        return joiner.join(part for part in continuation if part).strip() or None
    return _unquote(value) or None


def string_list(frontmatter: str, key: str) -> list[str] | None:
    """Read ``key: [a, b]`` or a ``- item`` block list; ``None`` when the key is absent."""
    match = re.search(rf"^[ \t]*{re.escape(key)}:[ \t]*(.*)$", frontmatter, re.M)
    if not match:
        return None
    inline = match.group(1).strip()
    if inline.startswith("["):
        inner = inline.strip("[]")
        return [_unquote(item) for item in inner.split(",") if item.strip()]
    items: list[str] = []
    for line in frontmatter[match.end() :].split("\n")[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("-"):
            break
        items.append(_unquote(stripped[1:]))
    return items
