"""On-disk results: ``results/<model>/<skill>/<timestamp>/{result.json,meta.json}``.

Every run is append-only. Trends across models and across time come from
reading the whole tree back, so nothing here ever rewrites a past run.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

RESULT_FILE = "result.json"
META_FILE = "meta.json"
LOG_FILE = "claude.log"
FINGERPRINT_IGNORE = {"evals", "results", ".git", "__pycache__", "node_modules", ".DS_Store"}


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def run_dir(results_dir: Path, model: str, skill: str, stamp: str) -> Path:
    return results_dir / model_slug(model) / skill / stamp


def dir_fingerprint(directory: Path, ignore: frozenset[str] = frozenset()) -> str:
    """Short content hash of a directory tree (paths and bytes), skipping ignored names."""
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if ignore.intersection(relative.parts) or not path.is_file():
            continue
        digest.update(str(relative).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def skill_fingerprint(skill_dir: Path) -> str:
    """Hash of the skill's content minus its evals, so a score change can be tied to a skill edit.

    Pair it with the evals fingerprint: same skill hash + different evals hash means the
    graders moved, not the model.
    """
    return dir_fingerprint(skill_dir, frozenset(FINGERPRINT_IGNORE))


@dataclass(frozen=True)
class Fingerprints:
    """What a run records about its inputs: the skill's content and the assembled evals."""

    skill: str
    evals: str


@dataclass(frozen=True)
class RunRecord:
    model: str
    skill: str
    stamp: str
    path: Path
    result: dict
    meta: dict

    @property
    def partial(self) -> bool:
        return bool(self.result.get("partial"))


def write_meta(directory: Path, meta: dict) -> Path:
    path = directory / META_FILE
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return path


def load_runs(
    results_dir: Path,
    *,
    models: Iterable[str] | None = None,
    skills: Iterable[str] | None = None,
) -> list[RunRecord]:
    wanted_models = {model_slug(m) for m in models} if models is not None else None
    wanted_skills = set(skills) if skills is not None else None
    records: list[RunRecord] = []
    if not results_dir.is_dir():
        return records
    for result_path in sorted(results_dir.glob(f"*/*/*/{RESULT_FILE}")):
        stamp_dir = result_path.parent
        slug, skill = stamp_dir.parent.parent.name, stamp_dir.parent.name
        if wanted_models is not None and slug not in wanted_models:
            continue
        if wanted_skills is not None and skill not in wanted_skills:
            continue
        try:
            result = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            continue
        meta_path = stamp_dir / META_FILE
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        model = meta.get("model") or result.get("suite", {}).get("modelOverride") or slug
        records.append(RunRecord(model, skill, stamp_dir.name, stamp_dir, result, meta))
    return records


def _newer(candidate: RunRecord, current: RunRecord | None) -> bool:
    """Complete runs beat partial ones; otherwise the newer stamp wins."""
    if current is None:
        return True
    if current.partial != candidate.partial:
        return current.partial
    return candidate.stamp > current.stamp


def latest(records: Iterable[RunRecord]) -> dict[tuple[str, str], RunRecord]:
    """Newest result per (model, skill), assembled case by case.

    A run may cover only some of a suite's cases (``run --case``), typically after a rubric
    fix. Each case therefore takes its newest complete result across all runs, so a
    single-case re-run updates that case without hiding the rest of the suite. The
    returned record carries the newest contributing run's stamp, path and meta, plus
    ``meta["merged_from"]`` when more than one run contributed.
    """
    by_key: dict[tuple[str, str], list[RunRecord]] = {}
    for record in records:
        by_key.setdefault((record.model, record.skill), []).append(record)
    merged: dict[tuple[str, str], RunRecord] = {}
    for key, runs in by_key.items():
        newest_per_case: dict[str, tuple[RunRecord, dict]] = {}
        for run in runs:
            for case in run.result.get("cases", []):
                held = newest_per_case.get(case["name"])
                if _newer(run, held[0] if held else None):
                    newest_per_case[case["name"]] = (run, case)
        head = max(runs, key=lambda r: (not r.partial, r.stamp))
        # The newest full-suite run defines which cases still exist; a renamed or deleted
        # case must not linger from older runs. Case-filtered runs only refresh members.
        full_runs = [r for r in runs if not r.meta.get("case_glob") and not r.meta.get("tags")]
        if full_runs:
            canonical = max(full_runs, key=lambda r: (not r.partial, r.stamp))
            wanted = {c["name"] for c in canonical.result.get("cases", [])}
            newest_per_case = {n: v for n, v in newest_per_case.items() if n in wanted}
        contributors = {run.stamp for run, _ in newest_per_case.values()}
        head_names = {c["name"] for c in head.result.get("cases", [])}
        if len(contributors) <= 1 and set(newest_per_case) == head_names:
            merged[key] = head
            continue
        result = dict(head.result)
        result["cases"] = [
            case for _, case in sorted(newest_per_case.values(), key=lambda t: t[1]["name"])
        ]
        result["costUsd"] = sum(
            run.result.get("costUsd", 0.0) for run in runs if run.stamp in contributors
        ) / max(1, len(contributors))
        meta = dict(head.meta)
        meta["merged_from"] = sorted(contributors)
        merged[key] = RunRecord(head.model, head.skill, head.stamp, head.path, result, meta)
    return merged


@dataclass(frozen=True)
class Staleness:
    """How a stored cell's inputs compare with the skill as it is on disk now."""

    model: str
    skill: str
    stamp: str  # newest run contributing to the cell
    skill_changed: bool
    evals_changed: bool
    unrecorded: bool  # a contributing run predates fingerprinting
    judge_changed: bool = False  # graded by a different judge than the config names now

    @property
    def stale(self) -> bool:
        return self.skill_changed or self.evals_changed or self.unrecorded or self.judge_changed

    @property
    def reason(self) -> str:
        parts = []
        if self.evals_changed:
            parts.append("graders changed")
        if self.skill_changed:
            parts.append("skill changed")
        if self.unrecorded:
            parts.append("no fingerprint recorded")
        if self.judge_changed:
            parts.append("judge changed")
        return ", ".join(parts) or "current"


def staleness(
    records: Iterable[RunRecord],
    current: Mapping[str, Fingerprints],
    *,
    judge: str | None = None,
) -> list[Staleness]:
    """Compare every latest cell with the fingerprints its skill would record today.

    A cell assembled from several runs (see ``latest``) is stale if any contributing run
    was graded with different inputs, so a single-case re-run on new graders does not
    hide that the rest of the suite still carries the old ones. Skills absent from
    ``current`` (no longer under the roots) are skipped. With ``judge`` given, a cell whose
    runs were graded by a different judge model is stale too: scores from two judges are
    not comparable, so the cell needs a re-run before it sits next to the others.
    """
    records = list(records)
    by_stamp = {(r.model, r.skill, r.stamp): r for r in records}
    cells: list[Staleness] = []
    for (model, skill), head in sorted(latest(records).items()):
        now = current.get(skill)
        if now is None:
            continue
        skill_changed = evals_changed = unrecorded = judge_changed = False
        for stamp in head.meta.get("merged_from") or [head.stamp]:
            run = by_stamp.get((model, skill, stamp), head)
            meta = run.meta
            recorded_judge = run.result.get("suite", {}).get("judgeModel")
            if judge and recorded_judge and recorded_judge != judge:
                judge_changed = True
            recorded_skill = meta.get("skill_fingerprint")
            recorded_evals = meta.get("evals_fingerprint")
            if recorded_skill is None or recorded_evals is None:
                unrecorded = True
                continue
            skill_changed = skill_changed or recorded_skill != now.skill
            evals_changed = evals_changed or recorded_evals != now.evals
        cells.append(
            Staleness(
                model, skill, head.stamp, skill_changed, evals_changed, unrecorded, judge_changed
            )
        )
    return cells
