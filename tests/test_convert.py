import json
from pathlib import Path

from skillbench.convert import convert_evals_json, slugify, yaml_block


def test_slugify_and_block():
    assert slugify("Buggy Cache (understand)!") == "buggy-cache-understand"
    assert yaml_block("a\n\nb", 2) == "|\n  a\n\n  b"


def test_convert_writes_cases_and_fixtures(tmp_path: Path):
    skill = tmp_path / "tutor"
    (skill / "evals").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: tutor\n---\n")
    (skill / "evals" / "fixtures").mkdir()
    (skill / "evals" / "fixtures" / "cache.py").write_text("x = 1\n")
    (skill / "evals" / "evals.json").write_text(
        json.dumps(
            {
                "skill_name": "tutor",
                "evals": [
                    {
                        "id": 0,
                        "name": "buggy-cache",
                        "prompt": 'Teach me: "quotes" and $dollars\nsecond line',
                        "expected_output": "Opens a teaching session.",
                        "files": ["fixtures/cache.py", "fixtures/missing.py"],
                        "expectations": ["Asks a question first"],
                    },
                    {"id": 1, "prompt": "No name here", "expected_output": ""},
                ],
            }
        )
    )
    result = convert_evals_json(skill)
    names = sorted(p.relative_to(skill / "evals").as_posix() for p in result.written)
    assert "buggy-cache/case.yaml" in names
    assert "buggy-cache/setup.sh" in names
    assert "buggy-cache/graders/skill-fired.md" in names
    assert "buggy-cache/graders/expected-output.md" in names
    assert "buggy-cache/graders/expectation-1.md" in names
    assert "eval-1/case.yaml" in names
    assert not any(n.startswith("eval-1/graders/expected") for n in names)
    assert any("missing.py" in w for w in result.warnings)

    case_yaml = (skill / "evals" / "buggy-cache" / "case.yaml").read_text()
    assert 'schema_version: "1.1"' in case_yaml
    assert "scaffold_script: setup.sh" in case_yaml
    assert '    Teach me: "quotes" and $dollars\n    second line' in case_yaml
    assert (skill / "evals" / "buggy-cache" / "fixtures" / "cache.py").read_text() == "x = 1\n"
    fired = (skill / "evals" / "buggy-cache" / "graders" / "skill-fired.md").read_text()
    assert "tool: Skill" in fired and "tutor" in fired

    again = convert_evals_json(skill)
    assert again.skipped == ["buggy-cache", "eval-1"]
    forced = convert_evals_json(skill, force=True)
    assert forced.skipped == []


EVAL_MD = """# When to use the MCP lane

## Prompt

The Runpod MCP tools are connected. I need to: (1) list my endpoints,
(2) deploy a worker. Handle each.

## Expected behavior

Per `runpod-mcp/SKILL.md`:

1. list endpoints → runpod-mcp
2. deploy → runpod-mcp

## Assertions

- Routes the endpoint **list** to runpod-mcp.
- Routes the deploy to runpod-mcp via `deploy-hub-repo`
  (optionally `list-hub-repos` first).
- Does NOT fall back to runpodctl while MCP is connected.
"""


def test_convert_eval_md(tmp_path: Path):
    from skillbench.convert import convert_skill, detect_format

    skill = tmp_path / "runpod-mcp"
    (skill / "evals").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: runpod-mcp\n---\n")
    (skill / "evals" / "when-to-use-mcp.eval.md").write_text(EVAL_MD)
    (skill / "evals" / "empty.eval.md").write_text("# nothing here\n")
    assert detect_format(skill) == "eval.md"
    out = tmp_path / "suites" / "runpod-mcp" / "evals"
    result = convert_skill(skill, out_dir=out)
    names = sorted(p.relative_to(out).as_posix() for p in result.written)
    assert names == [
        "when-to-use-mcp/case.yaml",
        "when-to-use-mcp/graders/assertion-1.md",
        "when-to-use-mcp/graders/assertion-2.md",
        "when-to-use-mcp/graders/assertion-3.md",
        "when-to-use-mcp/graders/skill-fired.md",
    ]
    assert any("empty.eval.md" in w for w in result.warnings)
    case_yaml = (out / "when-to-use-mcp" / "case.yaml").read_text()
    assert "The Runpod MCP tools are connected. I need to: (1) list my endpoints," in case_yaml
    assert "expected_outcome: |" in case_yaml
    second = (out / "when-to-use-mcp" / "graders" / "assertion-2.md").read_text()
    # A wrapped bullet is joined back into one assertion.
    assert (
        "(optionally `list-hub-repos` first)" in second and "PASS if: Routes the deploy" in second
    )
    assert "focus: trace" in second
    assert not (skill / "evals" / "when-to-use-mcp").exists()  # third-party dir untouched
