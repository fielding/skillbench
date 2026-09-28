import json
from pathlib import Path

from skillbench.config import Config
from skillbench.dashboard import DATA_MARKER, build_payload, render, write_dashboard
from skillbench.store import RESULT_FILE, run_dir, write_meta
from tests.conftest import make_skill


def _store(results: Path, model: str, skill: str, stamp: str, result: dict, **meta) -> Path:
    d = run_dir(results, model, skill, stamp)
    d.mkdir(parents=True)
    (d / RESULT_FILE).write_text(json.dumps(result))
    (d / "report.html").write_text("<html></html>")
    write_meta(d, {"model": model, **meta})
    return d


def test_payload_shape(tmp_path: Path, two_arm: dict, one_arm: dict):
    cfg = Config(root=tmp_path, models=("claude-opus-5", "claude-sonnet-5"))
    _store(
        cfg.results_dir,
        "claude-haiku-4-5",
        "tutor",
        "2026-01-01T00-00-00Z",
        two_arm,
        skill_fingerprint="abc",
        evals_fingerprint="def",
        duration_seconds=12.5,
    )
    _store(cfg.results_dir, "claude-opus-5", "tutor", "2026-01-02T00-00-00Z", one_arm)
    payload = build_payload(cfg)
    # Config order first, then models only seen in results; models with no runs are dropped.
    assert payload["models"] == ["claude-opus-5", "claude-haiku-4-5"]
    assert payload["skills"] == ["tutor"]
    assert [r["stamp"] for r in payload["runs"]] == ["2026-01-01T00-00-00Z", "2026-01-02T00-00-00Z"]
    haiku = payload["runs"][0]
    assert haiku["score"] == 1.0 and haiku["delta"] == 1.0
    assert haiku["skill_fp"] == "abc" and haiku["evals_fp"] == "def"
    assert haiku["report"] == "claude-haiku-4-5/tutor/2026-01-01T00-00-00Z/report.html"
    case = haiku["cases"][0]
    assert case["name"] == "greet" and set(case["arms"]) == {"with", "without"}
    grader = case["arms"]["with"][0]["graders"][0]
    assert {"name", "passed", "scored", "explanation", "evidence"} <= set(grader)
    assert payload["runs"][1]["delta"] is None
    # One cell per (model, skill), assembled by store.latest(); no roots here, so no staleness.
    assert [(c["model"], c["skill"]) for c in payload["cells"]] == [
        ("claude-haiku-4-5", "tutor"),
        ("claude-opus-5", "tutor"),
    ]
    assert payload["cells"][0]["stale"] is None


def test_render_embeds_escaped_json(tmp_path: Path, two_arm: dict):
    cfg = Config(root=tmp_path)
    two_arm = json.loads(json.dumps(two_arm))
    two_arm["cases"][0]["promptMarkdown"] = "say </script><b>hi</b>"
    _store(cfg.results_dir, "claude-opus-5", "tutor", "2026-01-01T00-00-00Z", two_arm)
    html = render(build_payload(cfg))
    assert DATA_MARKER not in html
    assert "</script><b>hi" not in html
    assert "<\\/script><b>hi<\\/b>" in html
    assert "const LIVE = false;" in html
    assert "const LIVE = true;" in render(build_payload(cfg), live=True)
    out = write_dashboard(cfg)
    assert out == cfg.results_dir / "dashboard.html" and out.stat().st_size > 1000


def test_payload_catalog_lists_every_skill(tmp_path: Path, two_arm: dict):
    root = tmp_path / "skills"
    make_skill(root, "alpha", cases=("one",))
    make_skill(root, "beta")
    cfg = Config(root=tmp_path, roots=(root,), models=("claude-opus-5",))
    _store(cfg.results_dir, "claude-opus-5", "alpha", "2026-01-01T00-00-00Z", two_arm)
    catalog = {c["name"]: c for c in build_payload(cfg)["catalog"]}
    assert set(catalog) == {"alpha", "beta"}
    assert catalog["alpha"]["cases"] == 1 and catalog["alpha"]["runs"] == 1
    assert catalog["alpha"]["models"] == ["claude-opus-5"] and catalog["alpha"]["sources"] == [
        "in-skill"
    ]
    assert catalog["beta"] == {
        "name": "beta",
        "path": catalog["beta"]["path"],
        "cases": 0,
        "sources": [],
        "runs": 0,
        "models": [],
        "last_run": None,
    }


def test_payload_cells_flag_stale_inputs(tmp_path: Path, two_arm: dict):
    root = tmp_path / "skills"
    make_skill(root, "alpha", cases=("one",))
    cfg = Config(root=tmp_path, roots=(root,), models=("claude-opus-5",))
    _store(
        cfg.results_dir,
        "claude-opus-5",
        "alpha",
        "2026-01-01T00-00-00Z",
        two_arm,
        skill_fingerprint="stale-skill",
        evals_fingerprint="stale-evals",
    )
    (cell,) = build_payload(cfg)["cells"]
    assert cell["stale"]["stale"] is True
    # The fixture was judged by "haiku"; the default config judge differs, so that counts too.
    assert cell["stale"]["reason"] == "graders changed, skill changed, judge changed"
    assert cell["merged_from"] is None
