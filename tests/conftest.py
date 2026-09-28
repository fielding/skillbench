import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def two_arm() -> dict:
    return json.loads((FIXTURES / "two_arm.json").read_text())


@pytest.fixture
def one_arm() -> dict:
    return json.loads((FIXTURES / "one_arm.json").read_text())


@pytest.fixture
def errored() -> dict:
    return json.loads((FIXTURES / "errored.json").read_text())


def make_skill(root: Path, name: str, *, cases: tuple[str, ...] = ()) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n# {name}\n"
    )
    (skill / "references").mkdir()
    (skill / "references" / "notes.md").write_text("ref\n")
    for case in cases:
        case_dir = skill / "evals" / case
        (case_dir / "graders").mkdir(parents=True)
        (case_dir / "prompt.md").write_text(
            "---\nmax_turns: 4\nallowed_tools: [Read, Skill]\n---\n\nDo the thing.\n"
        )
        (case_dir / "graders" / "check.md").write_text("---\ntype: regex\npattern: ok\n---\n")
    return skill


@pytest.fixture
def skill_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    make_skill(root, "alpha", cases=("one", "two"))
    make_skill(root, "beta")
    return root
