"""Build the throwaway plugin that `claude plugin eval` actually runs.

The eval command wants a plugin directory. Rather than write into skill repos
(some are third-party, some are checked out without git), each run copies the
skill into ``build/<skill>/skills/<skill>`` behind a minimal manifest and
assembles ``build/<skill>/evals`` from the skill's own ``evals/`` plus any
overlay suite in this repo. Symlinks are not an option: the eval command refuses
an eval directory that links outside the plugin.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .discover import Skill, list_cases
from .store import Fingerprints, dir_fingerprint, skill_fingerprint

# Dropped from the skill copy: its evals stay hidden from the agent under test, and
# results/VCS/caches are noise.
SKILL_IGNORE = shutil.ignore_patterns(
    "evals", "results", ".git", "__pycache__", "node_modules", ".DS_Store", ".venv"
)
CASE_IGNORE = shutil.ignore_patterns("results", "__pycache__", ".DS_Store")
# Hashed over the assembled evals/ tree; results/ is what the eval command writes into it.
EVALS_FINGERPRINT_IGNORE = frozenset({"results"})


def materialize(skill: Skill, build_dir: Path) -> Path:
    dest = build_dir / skill.name
    if dest.exists():
        shutil.rmtree(dest)
    manifest_dir = dest / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    manifest = {
        "name": skill.name,
        "description": f"skillbench wrapper around the {skill.name} skill",
        "version": "0.0.0",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")

    shutil.copytree(skill.path, dest / "skills" / skill.name, ignore=SKILL_IGNORE)

    evals_dest = dest / "evals"
    evals_dest.mkdir()
    # In-skill cases first, overlay last: an overlay case with the same name wins.
    for evals_dir in skill.evals:
        for case_dir in list_cases(evals_dir):
            target = evals_dest / case_dir.relative_to(evals_dir)
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(case_dir, target, ignore=CASE_IGNORE)
        mocks = evals_dir / "mocks"
        if mocks.is_dir():
            shutil.copytree(mocks, evals_dest / "mocks", dirs_exist_ok=True)
    return dest


def evals_fingerprint(plugin_dir: Path) -> str:
    """Hash of the assembled ``evals/`` tree, i.e. exactly what the eval command graded with."""
    return dir_fingerprint(plugin_dir / "evals", EVALS_FINGERPRINT_IGNORE)


def fingerprints(skill: Skill) -> Fingerprints:
    """The fingerprints a run of this skill would record right now.

    Assembles the plugin in a temporary directory rather than ``build/``, so a run in
    progress keeps the tree it is grading.
    """
    with tempfile.TemporaryDirectory(prefix="skillbench-fp-") as tmp:
        plugin_dir = materialize(skill, Path(tmp))
        return Fingerprints(
            skill=skill_fingerprint(skill.path), evals=evals_fingerprint(plugin_dir)
        )
