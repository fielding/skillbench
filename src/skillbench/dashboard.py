"""Build the self-contained HTML dashboard from stored results.

The page is a static template shipped with the package; the only thing rendered
server-side is the JSON payload it reads. Everything a reader might drill into
(per-run grader verdicts, judge evidence excerpts) is embedded, so the file works
offline and can be committed or shared as-is.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from . import __version__
from .config import Config
from .discover import discover
from .materialize import fingerprints
from .report import summarize
from .store import RunRecord, latest, load_runs, staleness

TEMPLATE = "dashboard.html"
DATA_MARKER = "/*__DATA__*/null"
LIVE_MARKER = "__LIVE__"
TEXT_LIMIT = 1500


def _clip(text: object) -> str:
    text = str(text or "")
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


def _arm_payload(runs: list[dict]) -> list[dict]:
    return [
        {
            "score": run.get("score"),
            "turns": run.get("turns"),
            "error": run.get("error"),
            "cost": run.get("costUsd"),
            "duration": run.get("durationSeconds"),
            "graders": [
                {
                    "name": g.get("name"),
                    "passed": bool(g.get("passed")),
                    "scored": g.get("scored", True),
                    "explanation": _clip(g.get("explanation")),
                    "evidence": _clip(g.get("evidence")),
                }
                for g in run.get("graders", [])
            ],
        }
        for run in runs
    ]


def run_payload(record: RunRecord, results_dir: Path) -> dict:
    summary = summarize(record.result)
    raw_cases = record.result.get("cases", [])
    cases = []
    for raw, case in zip(raw_cases, summary.cases, strict=True):
        cases.append(
            {
                "name": case.name,
                "score": case.score,
                "score_without": case.score_without,
                "delta": case.delta,
                "pass_rate": case.pass_rate,
                "fired": case.fired,
                "runs": case.runs,
                "errors": case.errors,
                "prompt": _clip(raw.get("promptMarkdown")),
                "graders": [
                    {"name": g.get("name"), "type": g.get("type"), "weight": g.get("weight", 1)}
                    for g in raw.get("graders", [])
                ],
                "arms": {arm: _arm_payload(runs) for arm, runs in raw.get("arms", {}).items()},
            }
        )
    try:
        relative = record.path.relative_to(results_dir)
    except ValueError:
        relative = record.path
    report = record.path / "report.html"
    return {
        "model": record.model,
        "skill": record.skill,
        "stamp": record.stamp,
        "path": relative.as_posix(),
        "report": (relative / "report.html").as_posix() if report.is_file() else None,
        "partial": summary.partial,
        "provider": record.meta.get("provider"),
        # Claude Code's cost estimate assumes Claude pricing; through a proxy it is meaningless.
        "cost": None if record.meta.get("cost_unreliable") else summary.cost_usd,
        "duration": record.meta.get("duration_seconds"),
        "claude_version": summary.claude_version,
        "judge": summary.judge_model,
        "ablation": summary.ablation,
        "runs_per_case": (raw_cases[0].get("runsPerCase") if raw_cases else None),
        "score": summary.score,
        "score_without": summary.score_without,
        "delta": summary.delta,
        "errors": summary.errors,
        "skill_fp": record.meta.get("skill_fingerprint"),
        "evals_fp": record.meta.get("evals_fingerprint"),
        "merged_from": record.meta.get("merged_from"),
        "cases": cases,
    }


def build_payload(config: Config) -> dict:
    records = sorted(load_runs(config.results_dir), key=lambda r: r.stamp)
    runs = [run_payload(r, config.results_dir) for r in records]
    skills_found = discover(config, include_empty=True)
    with_runs = {r.skill for r in records}
    # What each skill's graders and content hash to right now, so a cell whose inputs moved
    # since its run can say so instead of quietly showing an old score.
    current = {s.name: fingerprints(s) for s in skills_found if s.name in with_runs}
    stale = {(c.model, c.skill): c for c in staleness(records, current, judge=config.judge_for)}
    cells = []
    for (model, skill), record in sorted(latest(records).items()):
        cell = run_payload(record, config.results_dir)
        mark = stale.get((model, skill))
        cell["stale"] = (
            {**asdict(mark), "stale": mark.stale, "reason": mark.reason} if mark else None
        )
        cells.append(cell)
    # Config order first so columns match skillbench.toml; anything else follows as seen.
    models = list(config.models)
    for run in runs:
        if run["model"] not in models:
            models.append(run["model"])
    models = [m for m in models if any(r["model"] == m for r in runs)]
    catalog = []
    for skill in skills_found:
        mine = [r for r in runs if r["skill"] == skill.name]
        catalog.append(
            {
                "name": skill.name,
                "path": _short(skill.path),
                "cases": len(skill.cases),
                "sources": [
                    label
                    for label, present in (
                        ("in-skill", skill.in_skill_evals),
                        ("overlay", skill.overlay_evals),
                    )
                    if present
                ],
                "runs": len(mine),
                "models": sorted({r["model"] for r in mine}),
                "last_run": max((r["stamp"] for r in mine), default=None),
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "version": __version__,
        "judge_model": config.judge_model,
        "config_models": list(config.models),
        "models": models,
        "skills": sorted({r["skill"] for r in runs}),
        "runs": runs,
        "cells": cells,
        "catalog": catalog,
    }


def _short(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def render(payload: dict, *, live: bool = False) -> str:
    template = resources.files("skillbench").joinpath("assets", TEMPLATE).read_text()
    if DATA_MARKER not in template:
        raise RuntimeError(f"dashboard template lacks {DATA_MARKER}")
    # `</` inside a <script> would end the element early; escaping it keeps the JSON valid.
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return template.replace(DATA_MARKER, data).replace(LIVE_MARKER, "true" if live else "false")


def write_dashboard(config: Config, out: Path | None = None) -> Path:
    out = out or config.results_dir / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build_payload(config)))
    return out
