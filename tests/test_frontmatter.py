from skillbench.frontmatter import scalar, split_frontmatter, string_list

DOC = """---
name: tutor
description: >-
  Teach the user to deeply
  understand a thing.
allowed_tools: [Read, Glob, "Skill"]
tags:
  - a
  - 'b'
---

# Body
"""


def test_split_frontmatter():
    fm, body = split_frontmatter(DOC)
    assert fm.startswith("name: tutor")
    assert body.startswith("# Body")


def test_split_without_frontmatter():
    assert split_frontmatter("plain text") == ("", "plain text")


def test_scalar_plain_and_folded():
    fm, _ = split_frontmatter(DOC)
    assert scalar(fm, "name") == "tutor"
    assert scalar(fm, "description") == "Teach the user to deeply understand a thing."
    assert scalar(fm, "missing") is None


def test_string_list_inline_and_block():
    fm, _ = split_frontmatter(DOC)
    assert string_list(fm, "allowed_tools") == ["Read", "Glob", "Skill"]
    assert string_list(fm, "tags") == ["a", "b"]
    assert string_list(fm, "nope") is None


def test_nested_key_in_case_yaml():
    text = (
        "execution:\n  max_turns: 8\n  allowed_tools: [Read, Write]\n"
        "context:\n  scaffold_script: setup.sh\n"
    )
    assert string_list(text, "allowed_tools") == ["Read", "Write"]
    assert scalar(text, "scaffold_script") == "setup.sh"
