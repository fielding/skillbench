import copy
import json
from pathlib import Path

from skillbench.report import (
    compare,
    regressions,
    render_cases,
    render_compare,
    render_history,
    render_matrix,
    summarize,
    to_json,
)
from skillbench.store import RunRecord


def record(model: str, skill: str, result: dict, stamp: str = "2026-01-01T00-00-00Z") -> RunRecord:
    return RunRecord(
        model, skill, stamp, Path("/dev/null"), result, {"skill_fingerprint": "abc123"}
    )


def test_summarize_two_arm(two_arm: dict):
    s = summarize(two_arm)
    assert s.score == 1.0
    assert s.score_without == 0.0
    assert s.delta == 1.0
    assert s.partial is False
    assert s.errors == 0
    assert s.claude_version == "2.1.281"
    assert s.ablation == "with-without"
    case = s.cases[0]
    assert case.name == "greet" and case.runs == 1 and case.pass_rate == 1.0


def test_summarize_one_arm_has_no_delta(one_arm: dict):
    s = summarize(one_arm)
    assert s.delta is None
    assert s.cases[0].delta is None
    assert s.score == 1.0


def test_summarize_counts_errors(errored: dict):
    # Two case entries, each with a failed with-arm and without-arm run.
    s = summarize(errored)
    assert s.errors == 4
    assert all(c.errors == 2 for c in s.cases)
    assert s.score == 0.0


def test_fired_indicator(two_arm: dict):
    result = copy.deepcopy(two_arm)
    case = result["cases"][0]
    case["graders"].append({"name": "fired", "type": "tool_used", "config": {"tool": "Skill"}})
    case["arms"]["with"][0]["graders"].append({"name": "fired", "passed": True, "scored": False})
    assert summarize(result).cases[0].fired == 1.0
    assert summarize(two_arm).cases[0].fired is None


def test_matrix_and_cases_render(two_arm: dict, one_arm: dict):
    newest = {
        ("claude-opus-5", "tutor"): record("claude-opus-5", "tutor", two_arm),
        ("claude-sonnet-5", "tutor"): record("claude-sonnet-5", "tutor", one_arm),
    }
    text = render_matrix(
        newest, ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"], ["tutor"]
    )
    assert "1.00 Δ+1.00" in text and "·" in text
    md = render_matrix(newest, ["claude-opus-5"], ["tutor"], markdown=True)
    assert md.startswith("| skill")
    cases = render_cases(newest[("claude-opus-5", "tutor")])
    assert "greet" in cases and "cost $" in cases
    payload = json.loads(to_json(newest))
    assert payload[0]["model"] == "claude-opus-5" and payload[0]["score"] == 1.0


def _with_scores(base: dict, scores: dict[str, float]) -> dict:
    result = copy.deepcopy(base)
    template = result["cases"][0]
    result["cases"] = []
    for name, score in scores.items():
        case = copy.deepcopy(template)
        case["name"] = name
        case["aggregates"]["score"] = score
        result["cases"].append(case)
    return result


def test_compare_flags_regressions(two_arm: dict):
    baseline = record(
        "claude-opus-5", "tutor", _with_scores(two_arm, {"a": 1.0, "b": 0.9, "c": 0.5})
    )
    candidate = record(
        "claude-sonnet-5", "tutor", _with_scores(two_arm, {"a": 0.6, "b": 0.9, "d": 1.0})
    )
    newest = {("claude-opus-5", "tutor"): baseline, ("claude-sonnet-5", "tutor"): candidate}
    diffs = compare(newest, baseline="claude-opus-5", candidate="claude-sonnet-5")
    assert [d.case for d in diffs] == ["a", "b", "c", "d"]
    lost = regressions(diffs, min_drop=0.15)
    assert [(d.case, round(d.change, 2)) for d in lost] == [("a", -0.4)]
    text = render_compare(diffs, min_drop=0.15)
    assert "REGRESSION" in text and "1 regression(s)" in text
    assert render_compare([], min_drop=0.15).startswith("no overlapping")


def test_history_render(two_arm: dict):
    text = render_history([record("claude-opus-5", "tutor", two_arm)])
    assert "abc123" in text and "claude-opus-5" in text
