from pathlib import Path

import pytest

from skillbench.config import Config
from skillbench.discover import discover, find_skill_dirs, list_cases, skill_name


def test_find_skill_dirs_handles_root_that_is_a_skill(skill_root: Path):
    assert [p.name for p in find_skill_dirs([skill_root])] == ["alpha", "beta"]
    assert [p.name for p in find_skill_dirs([skill_root / "alpha"])] == ["alpha"]
    assert find_skill_dirs([skill_root / "missing"]) == []


def test_find_skill_dirs_dedupes_symlinks(skill_root: Path, tmp_path: Path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "alpha").symlink_to(skill_root / "alpha")
    found = find_skill_dirs([skill_root, store])
    assert [p.name for p in found] == ["alpha", "beta"]


def test_list_cases_skips_results_and_graders(skill_root: Path):
    evals = skill_root / "alpha" / "evals"
    (evals / "results" / "2026").mkdir(parents=True)
    (evals / "results" / "2026" / "prompt.md").write_text("not a case")
    assert [c.name for c in list_cases(evals)] == ["one", "two"]
    assert list_cases(skill_root / "beta" / "evals") == []


def test_discover_with_overlay(skill_root: Path, tmp_path: Path):
    cfg = Config(root=tmp_path, roots=(skill_root,))
    overlay = cfg.suites_dir / "beta" / "evals" / "extra"
    overlay.mkdir(parents=True)
    (overlay / "prompt.md").write_text("---\n---\nhi\n")
    skills = discover(cfg)
    by_name = {s.name: s for s in skills}
    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"].cases == ("one", "two")
    assert by_name["alpha"].in_skill_evals is not None
    assert by_name["beta"].cases == ("extra",)
    assert by_name["beta"].overlay_evals is not None
    assert by_name["beta"].in_skill_evals is None


def test_discover_filters_and_rejects_unknown(skill_root: Path, tmp_path: Path):
    cfg = Config(root=tmp_path, roots=(skill_root,))
    assert [s.name for s in discover(cfg, ["alpha"])] == ["alpha"]
    assert discover(cfg) and all(s.cases for s in discover(cfg))
    assert {s.name for s in discover(cfg, include_empty=True)} == {"alpha", "beta"}
    with pytest.raises(LookupError):
        discover(cfg, ["gamma"])


def test_skill_name_falls_back_to_dir(tmp_path: Path):
    skill = tmp_path / "dirname"
    skill.mkdir()
    (skill / "SKILL.md").write_text("no frontmatter here\n")
    assert skill_name(skill) == "dirname"
