"""Convert the eval formats skills already ship into native `claude plugin eval` cases.

Two formats are recognised:

- skill-creator (anthropics/skills) ``evals/evals.json``: prompt + expected_output +
  optional fixture files. Each becomes a ``case.yaml`` with an LLM grader built from
  the expected output, a ``tool_used: Skill`` grader as the fired indicator, and a
  scaffold script that copies fixtures into the sandboxed working directory (the
  only way a run can see them at a predictable path).
- ``evals/*.eval.md`` (as shipped by the runpod and flash skills): ``## Prompt``,
  ``## Expected behavior`` and an ``## Assertions`` bullet list. Each assertion becomes
  its own LLM grader so a failure names the exact expectation that broke.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .discover import skill_name

SETUP_SCRIPT = """#!/usr/bin/env bash
# Copies this case's fixtures into the run's throwaway working directory.
# `claude plugin eval` runs this as you (not sandboxed) with cwd already set there.
set -euo pipefail
cp -R "$(dirname "$0")/fixtures/." .
"""


@dataclass
class ConvertResult:
    written: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "case"


def yaml_string(text: str) -> str:
    # A JSON string literal is a valid YAML double-quoted scalar.
    return json.dumps(text)


def yaml_block(text: str, indent: int) -> str:
    pad = " " * indent
    lines = text.rstrip("\n").splitlines() or [""]
    return "|\n" + "\n".join(f"{pad}{line}" if line else "" for line in lines)


def skill_fired_grader(skill: str) -> str:
    pattern = rf'"skill"\s*:\s*"(?:[\w-]+:)?{re.escape(skill)}"'
    return f"---\ntype: tool_used\ntool: Skill\ninput_match: '{pattern}'\n---\n"


def llm_grader(criteria: str, *, focus: str, weight: int = 1) -> str:
    return f"---\ntype: llm\nfocus: {focus}\nweight: {weight}\n---\n\n{criteria.strip()}\n"


def _locate_fixture(skill_dir: Path, reference: str) -> Path | None:
    candidates = [
        skill_dir / reference,
        skill_dir / "evals" / reference,
        skill_dir / "evals" / "fixtures" / Path(reference).name,
        skill_dir / "evals" / "files" / Path(reference).name,
    ]
    return next((c for c in candidates if c.is_file()), None)


def convert_evals_json(
    skill_dir: Path,
    *,
    out_dir: Path | None = None,
    force: bool = False,
    focus: str = "trace",
    max_turns: int = 15,
    timeout_seconds: int = 600,
    runs: int = 3,
    allowed_tools: tuple[str, ...] = ("Read", "Glob", "Grep", "Skill"),
) -> ConvertResult:
    result = ConvertResult()
    evals_path = skill_dir / "evals" / "evals.json"
    data = json.loads(evals_path.read_text())
    skill = data.get("skill_name") or skill_name(skill_dir)
    out_dir = out_dir or skill_dir / "evals"

    for entry in data.get("evals", []):
        name = slugify(str(entry.get("name") or f"eval-{entry.get('id', 'x')}"))
        case_dir = out_dir / name
        if case_dir.exists() and not force:
            result.skipped.append(name)
            continue
        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "graders").mkdir(parents=True)

        fixtures: list[Path] = []
        for reference in entry.get("files", []):
            source = _locate_fixture(skill_dir, reference)
            if source is None:
                result.warnings.append(f"{name}: fixture {reference!r} not found; skipped")
                continue
            (case_dir / "fixtures").mkdir(exist_ok=True)
            fixtures.append(shutil.copy2(source, case_dir / "fixtures" / source.name))

        expected = str(entry.get("expected_output", "")).strip()
        lines = [
            'schema_version: "1.1"',
            f"name: {yaml_string(name)}",
            'description: "Converted from evals.json (skill-creator format) by skillbench."',
            f"expected_outcome: {yaml_block(expected, 2)}" if expected else "",
            f"runs: {runs}",
            "tags: [converted]",
            "execution:",
            f"  max_turns: {max_turns}",
            f"  timeout_seconds: {timeout_seconds}",
            f"  allowed_tools: [{', '.join(allowed_tools)}]",
            f"  prompt: {yaml_block(str(entry['prompt']), 4)}",
        ]
        if fixtures:
            lines += ["context:", "  scaffold_script: setup.sh"]
            setup = case_dir / "setup.sh"
            setup.write_text(SETUP_SCRIPT)
            setup.chmod(0o755)
            result.written.append(setup)
        case_yaml = case_dir / "case.yaml"
        case_yaml.write_text("\n".join(line for line in lines if line) + "\n")
        result.written.append(case_yaml)

        fired = case_dir / "graders" / "skill-fired.md"
        fired.write_text(skill_fired_grader(skill))
        result.written.append(fired)

        if expected:
            criteria = (
                "PASS if the run matches this expected behaviour, judged on what actually "
                f"happened rather than on claims:\n\n{expected}\n\nFAIL otherwise."
            )
            path = case_dir / "graders" / "expected-output.md"
            path.write_text(llm_grader(criteria, focus=focus, weight=2))
            result.written.append(path)

        for index, expectation in enumerate(entry.get("expectations", []), start=1):
            path = case_dir / "graders" / f"expectation-{index}.md"
            path.write_text(llm_grader(f"PASS if: {expectation}\nFAIL otherwise.", focus=focus))
            result.written.append(path)

    return result


EVAL_MD_SUFFIX = ".eval.md"


def detect_format(skill_dir: Path) -> str | None:
    evals = skill_dir / "evals"
    if (evals / "evals.json").is_file():
        return "evals.json"
    if evals.is_dir() and any(evals.glob(f"*{EVAL_MD_SUFFIX}")):
        return "eval.md"
    return None


def _sections(text: str) -> dict[str, str]:
    """Split a Markdown document on ``## `` headings; keys are lower-cased titles."""
    sections: dict[str, str] = {}
    title, buf = "_title", []
    for line in text.splitlines():
        if line.startswith("## "):
            sections[title] = "\n".join(buf).strip()
            title, buf = line[3:].strip().lower(), []
        else:
            buf.append(line)
    sections[title] = "\n".join(buf).strip()
    return sections


def _bullets(block: str) -> list[str]:
    items: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            items.append(stripped[2:].strip())
        elif items and stripped and line.startswith((" ", "\t")):
            items[-1] += " " + stripped  # wrapped continuation of the previous bullet
    return items


def convert_eval_md(
    skill_dir: Path,
    *,
    out_dir: Path | None = None,
    force: bool = False,
    focus: str = "trace",
    max_turns: int = 15,
    timeout_seconds: int = 600,
    runs: int = 3,
    allowed_tools: tuple[str, ...] = ("Read", "Glob", "Grep", "Skill"),
) -> ConvertResult:
    result = ConvertResult()
    skill = skill_name(skill_dir)
    out_dir = out_dir or skill_dir / "evals"
    for source in sorted((skill_dir / "evals").glob(f"*{EVAL_MD_SUFFIX}")):
        name = slugify(source.name[: -len(EVAL_MD_SUFFIX)])
        case_dir = out_dir / name
        if case_dir.exists() and not force:
            result.skipped.append(name)
            continue
        sections = _sections(source.read_text())
        prompt = sections.get("prompt", "").strip()
        if not prompt:
            result.warnings.append(f"{source.name}: no '## Prompt' section; skipped")
            continue
        expected = sections.get("expected behavior") or sections.get("expected behaviour") or ""
        assertions = _bullets(sections.get("assertions", ""))
        title = sections.get("_title", "").lstrip("# ").strip()

        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "graders").mkdir(parents=True)
        lines = [
            'schema_version: "1.1"',
            f"name: {yaml_string(name)}",
            f"description: {yaml_string((title + ' ' if title else '') + f'(from {source.name})')}",
            f"expected_outcome: {yaml_block(expected, 2)}" if expected else "",
            f"runs: {runs}",
            "tags: [converted]",
            "execution:",
            f"  max_turns: {max_turns}",
            f"  timeout_seconds: {timeout_seconds}",
            f"  allowed_tools: [{', '.join(allowed_tools)}]",
            f"  prompt: {yaml_block(prompt, 4)}",
        ]
        case_yaml = case_dir / "case.yaml"
        case_yaml.write_text("\n".join(line for line in lines if line) + "\n")
        result.written.append(case_yaml)
        fired = case_dir / "graders" / "skill-fired.md"
        fired.write_text(skill_fired_grader(skill))
        result.written.append(fired)
        if expected and not assertions:
            path = case_dir / "graders" / "expected-behavior.md"
            criteria = (
                f"PASS if the run shows this expected behaviour:\n\n{expected}\n\nFAIL otherwise."
            )
            path.write_text(llm_grader(criteria, focus=focus, weight=2))
            result.written.append(path)
        for index, assertion in enumerate(assertions, start=1):
            path = case_dir / "graders" / f"assertion-{index}.md"
            context = f"\n\nContext from the eval author:\n{expected}" if expected else ""
            path.write_text(
                llm_grader(f"PASS if: {assertion}\nFAIL otherwise.{context}", focus=focus)
            )
            result.written.append(path)
    return result


def convert_skill(skill_dir: Path, **kwargs) -> ConvertResult:
    fmt = detect_format(skill_dir)
    if fmt == "evals.json":
        return convert_evals_json(skill_dir, **kwargs)
    if fmt == "eval.md":
        return convert_eval_md(skill_dir, **kwargs)
    raise ValueError(f"{skill_dir} has no evals/evals.json and no evals/*.eval.md to convert")
