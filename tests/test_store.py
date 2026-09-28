import json
from pathlib import Path

from skillbench.store import (
    RESULT_FILE,
    dir_fingerprint,
    latest,
    load_runs,
    model_slug,
    run_dir,
    skill_fingerprint,
    write_meta,
)


def _store(
    results: Path, model: str, skill: str, stamp: str, result: dict, meta: dict | None = None
):
    d = run_dir(results, model, skill, stamp)
    d.mkdir(parents=True)
    (d / RESULT_FILE).write_text(json.dumps(result))
    write_meta(d, {"model": model, **(meta or {})})
    return d


def test_model_slug():
    assert model_slug("claude-opus-5") == "claude-opus-5"
    assert model_slug("weird/model name") == "weird-model-name"


def test_load_and_latest(tmp_path: Path, two_arm: dict, one_arm: dict):
    results = tmp_path / "results"
    _store(results, "claude-opus-5", "tutor", "2026-01-01T00-00-00Z", two_arm)
    _store(results, "claude-opus-5", "tutor", "2026-02-01T00-00-00Z", one_arm)
    partial = dict(two_arm, partial=True)
    _store(results, "claude-opus-5", "tutor", "2026-03-01T00-00-00Z", partial)
    _store(results, "claude-sonnet-5", "gate", "2026-01-15T00-00-00Z", two_arm)
    (results / "claude-opus-5" / "tutor" / "broken").mkdir()
    (results / "claude-opus-5" / "tutor" / "broken" / RESULT_FILE).write_text("{not json")

    records = load_runs(results)
    assert len(records) == 4
    assert {r.skill for r in records} == {"tutor", "gate"}
    assert len(load_runs(results, models=["claude-sonnet-5"])) == 1
    assert len(load_runs(results, skills=["tutor"])) == 3

    newest = latest(records)
    # A newer partial run must not shadow the newest complete run.
    assert newest[("claude-opus-5", "tutor")].stamp == "2026-02-01T00-00-00Z"
    assert newest[("claude-sonnet-5", "gate")].stamp == "2026-01-15T00-00-00Z"


def test_latest_merges_newest_result_per_case(tmp_path: Path, two_arm: dict):
    import copy

    results = tmp_path / "results"
    full = copy.deepcopy(two_arm)
    other = copy.deepcopy(full["cases"][0])
    other["name"] = "other"
    other["aggregates"]["score"] = 0.2
    full["cases"].append(other)
    _store(results, "claude-opus-5", "tutor", "2026-01-01T00-00-00Z", full)
    # A later run re-ran only "other" (after a rubric fix) and scored it higher.
    partial_suite = copy.deepcopy(two_arm)
    partial_suite["cases"][0]["name"] = "other"
    partial_suite["cases"][0]["aggregates"]["score"] = 0.9
    _store(
        results,
        "claude-opus-5",
        "tutor",
        "2026-01-02T00-00-00Z",
        partial_suite,
        {"case_glob": "other"},
    )

    merged = latest(load_runs(results))[("claude-opus-5", "tutor")]
    scores = {c["name"]: c["aggregates"]["score"] for c in merged.result["cases"]}
    assert scores == {"greet": 1.0, "other": 0.9}
    assert merged.stamp == "2026-01-02T00-00-00Z"
    assert merged.meta["merged_from"] == ["2026-01-01T00-00-00Z", "2026-01-02T00-00-00Z"]


def test_fingerprint_ignores_evals_and_changes_on_edit(skill_root: Path):
    alpha = skill_root / "alpha"
    before = skill_fingerprint(alpha)
    (alpha / "evals" / "one" / "prompt.md").write_text("changed prompt")
    assert skill_fingerprint(alpha) == before
    (alpha / "SKILL.md").write_text("---\nname: alpha\n---\nedited\n")
    assert skill_fingerprint(alpha) != before


def test_dir_fingerprint_tracks_eval_edits(skill_root: Path):
    evals = skill_root / "alpha" / "evals"
    before = dir_fingerprint(evals)
    (evals / "one" / "graders" / "check.md").write_text("---\ntype: regex\npattern: changed\n---\n")
    assert dir_fingerprint(evals) != before
    assert dir_fingerprint(evals, frozenset({"graders"})) == dir_fingerprint(
        evals, frozenset({"graders"})
    )


def test_latest_drops_cases_removed_from_the_suite(tmp_path: Path, two_arm: dict):
    import copy

    results = tmp_path / "results"
    old = copy.deepcopy(two_arm)
    gone = copy.deepcopy(old["cases"][0])
    gone["name"] = "renamed-away"
    old["cases"].append(gone)
    _store(results, "claude-opus-5", "tutor", "2026-01-01T00-00-00Z", old)
    # A later full-suite run no longer has "renamed-away".
    _store(results, "claude-opus-5", "tutor", "2026-01-02T00-00-00Z", copy.deepcopy(two_arm))
    merged = latest(load_runs(results))[("claude-opus-5", "tutor")]
    assert [c["name"] for c in merged.result["cases"]] == ["greet"]
    # A case-filtered run later still refreshes just its case.
    part = copy.deepcopy(two_arm)
    part["cases"][0]["aggregates"]["score"] = 0.4
    _store(results, "claude-opus-5", "tutor", "2026-01-03T00-00-00Z", part, {"case_glob": "greet"})
    merged = latest(load_runs(results))[("claude-opus-5", "tutor")]
    assert [c["aggregates"]["score"] for c in merged.result["cases"]] == [0.4]


def test_staleness_flags_any_contributing_run(tmp_path: Path):
    from skillbench.store import Fingerprints, staleness

    results = tmp_path / "results"
    suite = {"cases": [{"name": "a"}, {"name": "b"}]}
    old = {"skill_fingerprint": "s1", "evals_fingerprint": "e1"}
    _store(results, "claude-opus-5", "tutor", "2026-01-01T00-00-00Z", suite, old)
    # A single-case re-run on the new graders refreshes case a; case b still carries e1.
    _store(
        results,
        "claude-opus-5",
        "tutor",
        "2026-02-01T00-00-00Z",
        {"cases": suite["cases"][:1]},
        {"skill_fingerprint": "s1", "evals_fingerprint": "e2", "case_glob": "a"},
    )
    _store(
        results,
        "claude-sonnet-5",
        "gate",
        "2026-01-15T00-00-00Z",
        suite,
        {"skill_fingerprint": "g1", "evals_fingerprint": "ge1"},
    )
    _store(results, "claude-sonnet-5", "ancient", "2026-01-15T00-00-00Z", suite)  # no fingerprints
    _store(results, "claude-sonnet-5", "gone", "2026-01-15T00-00-00Z", suite, old)
    _store(
        results,
        "claude-sonnet-5",
        "judged",
        "2026-01-15T00-00-00Z",
        {**suite, "suite": {"judgeModel": "claude-haiku-4-5"}},
        {"skill_fingerprint": "j1", "evals_fingerprint": "je1"},
    )

    current = {
        "tutor": Fingerprints("s1", "e2"),
        "gate": Fingerprints("g1", "ge1"),
        "ancient": Fingerprints("x", "y"),
        "judged": Fingerprints("j1", "je1"),
    }
    cells = {
        (c.model, c.skill): c
        for c in staleness(load_runs(results), current, judge="claude-sonnet-5")
    }

    tutor = cells[("claude-opus-5", "tutor")]
    assert tutor.stale and tutor.evals_changed and not tutor.skill_changed
    assert tutor.stamp == "2026-02-01T00-00-00Z"
    assert tutor.reason == "graders changed"
    assert not cells[("claude-sonnet-5", "gate")].stale
    assert cells[("claude-sonnet-5", "gate")].reason == "current"
    ancient = cells[("claude-sonnet-5", "ancient")]
    assert ancient.unrecorded and ancient.reason == "no fingerprint recorded"
    judged = cells[("claude-sonnet-5", "judged")]
    assert judged.stale and judged.judge_changed and judged.reason == "judge changed"
    # A skill with results but no longer under the roots is skipped, not reported.
    assert ("claude-sonnet-5", "gone") not in cells
