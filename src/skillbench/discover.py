"""Find skills under the configured roots and the eval cases attached to each."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .frontmatter import scalar, split_frontmatter

CASE_FILES = ("prompt.md", "case.yaml")
# Directories `claude plugin eval` owns inside an eval dir; never cases themselves.
RESERVED = {"results", "mocks", "graders", "fixtures"}


@dataclass(frozen=True)
class Skill:
    name: str
    path: Path
    evals: tuple[Path, ...]
    cases: tuple[str, ...]

    @property
    def in_skill_evals(self) -> Path | None:
        return next((e for e in self.evals if e.parent == self.path), None)

    @property
    def overlay_evals(self) -> Path | None:
        return next((e for e in self.evals if e.parent != self.path), None)


def is_case_dir(path: Path) -> bool:
    return path.is_dir() and any((path / name).is_file() for name in CASE_FILES)


def list_cases(evals_dir: Path) -> list[Path]:
    if not evals_dir.is_dir():
        return []
    cases: list[Path] = []
    for path in sorted(evals_dir.rglob("*")):
        if not path.is_dir():
            continue
        relative = path.relative_to(evals_dir).parts
        if RESERVED.intersection(relative):
            continue
        if is_case_dir(path):
            cases.append(path)
    return cases


def case_name(evals_dir: Path, case_dir: Path) -> str:
    return "/".join(case_dir.relative_to(evals_dir).parts)


def skill_name(skill_dir: Path) -> str:
    frontmatter, _ = split_frontmatter((skill_dir / "SKILL.md").read_text())
    return scalar(frontmatter, "name") or skill_dir.name


def find_skill_dirs(roots: Iterable[Path]) -> list[Path]:
    """A root is either a skill itself (has SKILL.md) or a directory of skills."""
    seen: set[Path] = set()
    found: list[Path] = []
    for root in roots:
        root = root.expanduser()
        if not root.is_dir():
            continue
        if (root / "SKILL.md").is_file():
            candidates = [root]
        else:
            candidates = sorted(child for child in root.iterdir() if (child / "SKILL.md").is_file())
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(candidate)
    return found


def discover(
    config: Config, names: Iterable[str] | None = None, *, include_empty: bool = False
) -> list[Skill]:
    skills: list[Skill] = []
    for skill_dir in find_skill_dirs(config.roots):
        name = skill_name(skill_dir)
        eval_dirs = [skill_dir / "evals", config.suites_dir / name / "evals"]
        eval_dirs = [d for d in eval_dirs if list_cases(d)]
        cases = sorted({case_name(d, c) for d in eval_dirs for c in list_cases(d)})
        if not cases and not include_empty:
            continue
        skills.append(Skill(name, skill_dir, tuple(eval_dirs), tuple(cases)))

    if names is not None:
        wanted = list(names)
        by_name = {s.name: s for s in skills}
        unknown = [n for n in wanted if n not in by_name]
        if unknown:
            known = ", ".join(sorted(by_name)) or "(none)"
            raise LookupError(f"unknown skill(s) {unknown}; skills with cases: {known}")
        skills = [by_name[n] for n in wanted]
    return skills
