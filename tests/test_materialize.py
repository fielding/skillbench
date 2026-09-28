import json
from pathlib import Path

from skillbench.config import Config
from skillbench.discover import discover
from skillbench.materialize import materialize


def test_materialize_builds_wrapper_plugin(skill_root: Path, tmp_path: Path):
    cfg = Config(root=tmp_path, roots=(skill_root,))
    overlay = cfg.suites_dir / "alpha" / "evals"
    (overlay / "two" / "graders").mkdir(parents=True)
    (overlay / "two" / "prompt.md").write_text("---\n---\noverlay wins\n")
    (overlay / "three").mkdir()
    (overlay / "three" / "case.yaml").write_text('schema_version: "1.1"\nname: three\n')
    (skill_root / "alpha" / "evals" / "results" / "x").mkdir(parents=True)

    alpha = discover(cfg, ["alpha"])[0]
    plugin = materialize(alpha, cfg.build_dir)

    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "alpha"
    assert (plugin / "skills" / "alpha" / "SKILL.md").is_file()
    assert (plugin / "skills" / "alpha" / "references" / "notes.md").is_file()
    assert not (plugin / "skills" / "alpha" / "evals").exists()

    cases = sorted(p.name for p in (plugin / "evals").iterdir())
    assert cases == ["one", "three", "two"]
    assert (plugin / "evals" / "two" / "prompt.md").read_text().endswith("overlay wins\n")
    assert not (plugin / "evals" / "results").exists()

    # Re-materializing starts clean.
    (plugin / "stale.txt").write_text("x")
    materialize(alpha, cfg.build_dir)
    assert not (plugin / "stale.txt").exists()


def test_fingerprints_match_what_a_run_records(skill_root: Path, tmp_path: Path):
    from skillbench.materialize import evals_fingerprint, fingerprints
    from skillbench.store import skill_fingerprint

    cfg = Config(root=tmp_path, roots=(skill_root,))
    alpha = discover(cfg, ["alpha"])[0]
    before = fingerprints(alpha)
    plugin = materialize(alpha, cfg.build_dir)
    assert before.evals == evals_fingerprint(plugin)
    assert before.skill == skill_fingerprint(alpha.path)
    # Computed in a temp dir: build/ holds only what materialize itself put there.
    assert sorted(p.name for p in cfg.build_dir.iterdir()) == ["alpha"]

    grader = skill_root / "alpha" / "evals" / "one" / "graders" / "check.md"
    grader.write_text("---\ntype: regex\npattern: changed\n---\n")
    after_grader = fingerprints(alpha)
    assert after_grader.evals != before.evals
    assert after_grader.skill == before.skill

    (skill_root / "alpha" / "SKILL.md").write_text("---\nname: alpha\ndescription: edited\n---\n")
    after_skill = fingerprints(alpha)
    assert after_skill.skill != before.skill
    assert after_skill.evals == after_grader.evals
