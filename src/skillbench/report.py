"""Turn stored `claude plugin eval` results into per-case summaries, a skill × model
matrix, and baseline-versus-candidate comparisons."""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

from .store import RunRecord


@dataclass(frozen=True)
class CaseSummary:
    name: str
    score: float | None
    score_without: float | None
    delta: float | None
    pass_rate: float | None
    runs: int
    errors: int
    fired: float | None  # share of with-arm runs in which a `tool_used: Skill` grader passed


@dataclass(frozen=True)
class SuiteSummary:
    score: float | None
    score_without: float | None
    delta: float | None
    cost_usd: float
    partial: bool
    errors: int
    claude_version: str | None
    ablation: str | None
    judge_model: str | None
    cases: tuple[CaseSummary, ...]


def _mean(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.fmean(present) if present else None


def _skill_grader_names(case: dict) -> set[str]:
    return {
        g["name"]
        for g in case.get("graders", [])
        if g.get("type") == "tool_used" and g.get("config", {}).get("tool") == "Skill"
    }


def summarize_case(case: dict) -> CaseSummary:
    arms = case.get("arms", {})
    with_runs = arms.get("with", [])
    without_runs = arms.get("without", [])
    aggregates = case.get("aggregates", {})
    errors = sum(1 for run in (*with_runs, *without_runs) if run.get("error"))

    fired: float | None = None
    skill_graders = _skill_grader_names(case)
    if skill_graders and with_runs:
        hits = sum(
            1
            for run in with_runs
            if any(g["name"] in skill_graders and g.get("passed") for g in run.get("graders", []))
        )
        fired = hits / len(with_runs)

    return CaseSummary(
        name=case["name"],
        score=aggregates.get("score"),
        score_without=aggregates.get("scoreWithout"),
        delta=aggregates.get("delta") if without_runs else None,
        pass_rate=aggregates.get("passRate"),
        runs=len(with_runs),
        errors=errors,
        fired=fired,
    )


def summarize(result: dict) -> SuiteSummary:
    cases = tuple(summarize_case(c) for c in result.get("cases", []))
    suite = result.get("suite", {})
    return SuiteSummary(
        score=_mean(c.score for c in cases),
        score_without=_mean(c.score_without for c in cases),
        delta=_mean(c.delta for c in cases),
        cost_usd=float(result.get("costUsd", 0.0)),
        partial=bool(result.get("partial")),
        errors=sum(c.errors for c in cases),
        claude_version=result.get("claudeVersion"),
        ablation=suite.get("ablation"),
        judge_model=suite.get("judgeModel"),
        cases=cases,
    )


def fmt_score(value: float | None) -> str:
    return "  ·  " if value is None else f"{value:.2f}"


def fmt_delta(value: float | None) -> str:
    return "" if value is None else f" Δ{value:+.2f}"


def cell(record: RunRecord | None) -> str:
    if record is None:
        return "·"
    summary = summarize(record.result)
    flag = " !" if summary.partial or summary.errors else ""
    return f"{fmt_score(summary.score)}{fmt_delta(summary.delta)}{flag}".strip()


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], markdown: bool) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows, strict=True)]

    def line(cells: Sequence[str]) -> str:
        padded = [str(c).ljust(w) for c, w in zip(cells, widths, strict=True)]
        return "| " + " | ".join(padded) + " |" if markdown else "  ".join(padded).rstrip()

    out = [line(headers)]
    if markdown:
        out.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    else:
        out.append("  ".join("-" * w for w in widths))
    out.extend(line(r) for r in rows)
    return "\n".join(out)


def render_matrix(
    latest: dict[tuple[str, str], RunRecord],
    models: Sequence[str],
    skills: Sequence[str],
    *,
    markdown: bool = False,
) -> str:
    headers = ["skill", *models]
    rows = [[skill, *(cell(latest.get((m, skill))) for m in models)] for skill in skills]
    legend = (
        "score = with-skill mean (0..1); Δ = minus the no-skill baseline; ! = errors or partial"
    )
    return _table(headers, rows, markdown) + "\n" + legend


def render_cases(record: RunRecord, *, markdown: bool = False) -> str:
    summary = summarize(record.result)
    headers = ["case", "score", "w/out", "Δ", "pass%", "fired", "runs", "errors"]
    rows = []
    for c in summary.cases:
        rows.append(
            [
                c.name,
                fmt_score(c.score),
                fmt_score(c.score_without),
                "·" if c.delta is None else f"{c.delta:+.2f}",
                "·" if c.pass_rate is None else f"{c.pass_rate * 100:.0f}",
                "·" if c.fired is None else f"{c.fired * 100:.0f}%",
                str(c.runs),
                str(c.errors),
            ]
        )
    header = (
        f"{record.skill} @ {record.model} ({record.stamp})  "
        f"score {fmt_score(summary.score)}{fmt_delta(summary.delta)}  "
        f"cost ${summary.cost_usd:.2f}  claude {summary.claude_version or '?'}"
        f"{'  PARTIAL' if summary.partial else ''}"
    )
    return header + "\n" + _table(headers, rows, markdown)


@dataclass(frozen=True)
class CaseDiff:
    skill: str
    case: str
    baseline_model: str
    candidate_model: str
    baseline_score: float | None
    candidate_score: float | None

    @property
    def change(self) -> float | None:
        if self.baseline_score is None or self.candidate_score is None:
            return None
        return self.candidate_score - self.baseline_score


def compare(
    latest: dict[tuple[str, str], RunRecord], *, baseline: str, candidate: str
) -> list[CaseDiff]:
    diffs: list[CaseDiff] = []
    skills = sorted({skill for (model, skill) in latest if model in (baseline, candidate)})
    for skill in skills:
        base = latest.get((baseline, skill))
        cand = latest.get((candidate, skill))
        if base is None or cand is None:
            continue
        base_cases = {c.name: c for c in summarize(base.result).cases}
        cand_cases = {c.name: c for c in summarize(cand.result).cases}
        for name in sorted(set(base_cases) | set(cand_cases)):
            b = base_cases.get(name)
            c = cand_cases.get(name)
            diffs.append(
                CaseDiff(
                    skill,
                    name,
                    baseline,
                    candidate,
                    b.score if b else None,
                    c.score if c else None,
                )
            )
    return diffs


def regressions(diffs: Iterable[CaseDiff], *, min_drop: float) -> list[CaseDiff]:
    return [d for d in diffs if d.change is not None and -d.change >= min_drop]


def improvements(diffs: Iterable[CaseDiff], *, min_gain: float) -> list[CaseDiff]:
    return [d for d in diffs if d.change is not None and d.change >= min_gain]


def render_compare(diffs: Sequence[CaseDiff], *, min_drop: float, markdown: bool = False) -> str:
    if not diffs:
        return "no overlapping (skill, case) results to compare"
    baseline, candidate = diffs[0].baseline_model, diffs[0].candidate_model
    headers = ["skill", "case", baseline, candidate, "change", ""]
    rows = []
    for d in diffs:
        mark = ""
        if d.change is not None and -d.change >= min_drop:
            mark = "REGRESSION"
        elif d.change is not None and d.change >= min_drop:
            mark = "gain"
        rows.append(
            [
                d.skill,
                d.case,
                fmt_score(d.baseline_score),
                fmt_score(d.candidate_score),
                "·" if d.change is None else f"{d.change:+.2f}",
                mark,
            ]
        )
    lost = len(regressions(diffs, min_drop=min_drop))
    won = len(improvements(diffs, min_gain=min_drop))
    footer = f"{lost} regression(s), {won} gain(s) at |change| ≥ {min_drop:.2f}"
    return _table(headers, rows, markdown) + "\n" + footer


def render_history(records: Sequence[RunRecord], *, markdown: bool = False) -> str:
    headers = ["when", "model", "skill", "score", "Δ", "cost", "claude", "skill@", "evals@", "note"]
    rows = []
    for r in sorted(records, key=lambda r: r.stamp):
        s = summarize(r.result)
        note = "partial" if s.partial else (f"{s.errors} error(s)" if s.errors else "")
        rows.append(
            [
                r.stamp,
                r.model,
                r.skill,
                fmt_score(s.score),
                "·" if s.delta is None else f"{s.delta:+.2f}",
                f"${s.cost_usd:.2f}",
                s.claude_version or "?",
                r.meta.get("skill_fingerprint", "?"),
                r.meta.get("evals_fingerprint", "?"),
                note,
            ]
        )
    return _table(headers, rows, markdown)


def to_json(latest: dict[tuple[str, str], RunRecord]) -> str:
    payload = [
        {
            "model": model,
            "skill": skill,
            "stamp": record.stamp,
            "path": str(record.path),
            "skill_fingerprint": record.meta.get("skill_fingerprint"),
            **asdict(summarize(record.result)),
        }
        for (model, skill), record in sorted(latest.items())
    ]
    return json.dumps(payload, indent=2)
