"""skillbench command line: list, doctor, run, report, convert."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .config import GATED_TOOLS, Config, ConfigError, load_config
from .convert import convert_skill, detect_format
from .dashboard import write_dashboard
from .discover import Skill, discover, list_cases, skill_name
from .frontmatter import scalar, split_frontmatter, string_list
from .materialize import evals_fingerprint, fingerprints, materialize
from .proxy import ProxyError, describe_sources, group_by_provider, split_model, start_proxy
from .report import (
    compare,
    render_cases,
    render_compare,
    render_history,
    render_matrix,
    summarize,
    to_json,
)
from .runner import RunSpec, build_argv, child_env, execute
from .serve import serve
from .store import (
    LOG_FILE,
    RESULT_FILE,
    latest,
    load_runs,
    run_dir,
    skill_fingerprint,
    staleness,
    timestamp,
    write_meta,
)

MIN_CLAUDE = (2, 1, 269)  # first release with `claude plugin eval`
RUN_GUARD_CELLS = 40  # a bare `run` above this many (model, skill) cells needs --all


def _skills_or_exit(config: Config, names: list[str] | None) -> list[Skill]:
    try:
        skills = discover(config, names or None)
    except LookupError as exc:
        sys.exit(f"skillbench: {exc}")
    if not skills:
        sys.exit("skillbench: no skills with eval cases found under the configured roots")
    return skills


# ---------------------------------------------------------------- list


def _short(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def cmd_list(config: Config, args: argparse.Namespace) -> int:
    if args.stale:
        return _list_stale(config, args)
    everything = discover(config, include_empty=True)
    skills = [s for s in everything if s.cases] if args.cases_only else everything
    if args.json:
        payload = [
            {
                "name": s.name,
                "path": str(s.path),
                "evals": [str(e) for e in s.evals],
                "cases": list(s.cases),
                "overrides": config.overrides(s.name).__dict__,
            }
            for s in skills
        ]
        print(json.dumps(payload, indent=2))
        return 0
    if not skills:
        print("no skills found under:", ", ".join(str(r) for r in config.roots) or "(no roots)")
        return 0
    covered = [s for s in skills if s.cases]
    # Skills with cases first; the rest are what still needs a suite.
    skills = covered + [s for s in skills if not s.cases]
    for s in skills:
        sources = []
        if s.in_skill_evals:
            sources.append("in-skill")
        if s.overlay_evals:
            sources.append("overlay")
        o = config.overrides(s.name)
        extras = []
        if o.allow_tools:
            extras.append("allow " + ",".join(o.allow_tools))
        if o.scaffold:
            extras.append("scaffold")
        if o.skip:
            extras.append("SKIP")
        cases = f"{len(s.cases):>2} case(s)" if s.cases else " no cases"
        print(
            f"{s.name:<26} {cases}  {'+'.join(sources) or '-':<16} {_short(s.path)}"
            f"{'  [' + '; '.join(extras) + ']' if extras else ''}"
        )
        if args.verbose:
            for c in s.cases:
                print(f"{'':<26}    - {c}")
    print(f"\n{len(covered)} of {len(everything)} skill(s) have eval cases")
    return 0


def _list_stale(config: Config, args: argparse.Namespace) -> int:
    """Cells whose skill or graders changed after their newest run, with the re-run command."""
    records = load_runs(config.results_dir)
    if not records:
        print("no stored runs under", _short(config.results_dir))
        return 0
    with_runs = {r.skill for r in records}
    current = {
        s.name: fingerprints(s) for s in discover(config, include_empty=True) if s.name in with_runs
    }
    cells = staleness(records, current, judge=config.judge_model)
    stale = [c for c in cells if c.stale]
    if args.json:
        rows = [asdict(c) | {"stale": c.stale, "reason": c.reason} for c in cells]
        print(json.dumps(rows, indent=2))
        return 0
    if not stale:
        print(f"all {len(cells)} cell(s) current: latest runs match the skill and graders on disk")
        return 0
    print("stale cells (skill or graders edited after the newest run):")
    for c in stale:
        print(f"  {c.model:<24} {c.skill:<24} {c.stamp}  {c.reason}")
    by_model: dict[str, list[str]] = {}
    for c in stale:
        by_model.setdefault(c.model, []).append(c.skill)
    print(f"\n{len(stale)} of {len(cells)} cell(s) stale; re-run:")
    for model, names in by_model.items():
        flags = "".join(f" -s {n}" for n in names)
        print(f"  uv run skillbench run -m {shlex.quote(model)}{flags}")
    return 0


# ---------------------------------------------------------------- models


def cmd_models(config: Config, args: argparse.Namespace) -> int:
    """List what an OpenAI-compatible provider serves, flagging tool-calling support."""
    import urllib.request

    provider = config.providers.get(args.provider)
    if provider is None:
        sys.exit(f"skillbench: no [providers.{args.provider}] in config")
    url = f"{provider.base_url}/models"
    if args.provider == "venice":
        url += "?type=text"  # Venice's public catalog; image/audio models are noise here
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read())
    except OSError as exc:
        sys.exit(f"skillbench: could not fetch {url}: {exc}")
    rows = []
    for item in data.get("data", data if isinstance(data, list) else []):
        spec = item.get("model_spec", {})
        caps = spec.get("capabilities", {})
        rows.append(
            (
                item.get("id", "?"),
                caps.get("supportsFunctionCalling"),
                caps.get("supportsReasoning"),
                spec.get("availableContextTokens"),
                ",".join(spec.get("traits", []) or []),
            )
        )
    rows.sort()
    print(f"{'model id':<40} {'tools':<6} {'reason':<7} {'context':>9}  traits")
    for model_id, tools, reason, ctx, traits in rows:
        if args.tools_only and tools is False:
            continue
        flag = {True: "yes", False: "no", None: "?"}
        name = f"{args.provider}/{model_id}"
        print(f"{name:<40} {flag[tools]:<6} {flag[reason]:<7} {ctx or '?':>9}  {traits}")
    return 0


# ---------------------------------------------------------------- doctor


def _claude_version(claude_bin: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([claude_bin, "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out.stdout + out.stderr)
    return tuple(int(x) for x in match.groups()) if match else None


def _case_frontmatter(case_dir: Path) -> str:
    text = ""
    for name in ("prompt.md", "case.yaml"):
        path = case_dir / name
        if path.is_file():
            content = path.read_text()
            text += split_frontmatter(content)[0] if name.endswith(".md") else content
            text += "\n"
    return text


GRADER_TYPES = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}


def _case_problems(case_dir: Path) -> list[str]:
    """Cheap structural checks on a case; the eval command is the real validator."""
    problems: list[str] = []
    case_yaml = case_dir / "case.yaml"
    prompt_md = case_dir / "prompt.md"
    if case_yaml.is_file():
        text = case_yaml.read_text()
        if 'schema_version: "1.1"' not in text and "schema_version: '1.1'" not in text:
            problems.append('case.yaml lacks schema_version: "1.1"')
        if "prompt:" not in text and not prompt_md.is_file():
            problems.append("no prompt (execution.prompt in case.yaml or prompt.md)")
    graders_dir = case_dir / "graders"
    if graders_dir.is_dir():
        for grader in sorted(graders_dir.glob("*.md")):
            fm, body = split_frontmatter(grader.read_text())
            gtype = scalar(fm, "type")
            if gtype not in GRADER_TYPES:
                problems.append(
                    f"graders/{grader.name}: type {gtype!r} is not one of {sorted(GRADER_TYPES)}"
                )
                continue
            if gtype == "regex":
                pattern = scalar(fm, "pattern")
                if not pattern:
                    problems.append(f"graders/{grader.name}: regex grader without pattern")
                else:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        problems.append(f"graders/{grader.name}: pattern does not compile ({exc})")
            if gtype == "tool_used" and not scalar(fm, "tool"):
                problems.append(f"graders/{grader.name}: tool_used grader without tool")
            if gtype == "file_exists" and not scalar(fm, "path"):
                problems.append(f"graders/{grader.name}: file_exists grader without path")
            if gtype == "llm" and not body.strip() and not scalar(fm, "criteria"):
                problems.append(f"graders/{grader.name}: llm grader with no rubric")
    return problems


def cmd_doctor(config: Config, args: argparse.Namespace) -> int:
    problems = 0

    def ok(msg: str) -> None:
        print(f"  ok   {msg}")

    def warn(msg: str) -> None:
        print(f"  warn {msg}")

    def fail(msg: str) -> None:
        nonlocal problems
        problems += 1
        print(f"  FAIL {msg}")

    print(f"skillbench {__version__}  config: {config.root / 'skillbench.toml'}")
    if shutil.which(config.claude_bin) is None:
        fail(f"claude binary {config.claude_bin!r} not on PATH")
    else:
        version = _claude_version(config.claude_bin)
        if version is None:
            fail("could not parse `claude --version`")
        elif version < MIN_CLAUDE:
            fail(f"claude {'.'.join(map(str, version))} predates plugin eval (need ≥ 2.1.269)")
        else:
            ok(f"claude {'.'.join(map(str, version))}")
        auth = subprocess.run(
            [config.claude_bin, "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
            env=child_env(config.unset_env),
        )
        if '"loggedIn": true' in auth.stdout:
            ok("claude auth: logged in")
        else:
            fail("claude auth status did not report loggedIn=true (run `claude /login`)")

    for root in config.roots:
        (ok if root.is_dir() else fail)(f"root {root}")
    if not config.models:
        warn("no `models` in config; `run` needs --model")

    used = sorted({p for p in (split_model(m, config)[0] for m in config.models) if p})
    if used:
        if shutil.which(config.proxy.binary) is None:
            fail(
                f"[proxy] binary {config.proxy.binary!r} not on PATH; `{'/'.join(used)}/…` "
                "models cannot run (brew install cliproxyapi)"
            )
        else:
            ok(f"proxy binary {config.proxy.binary}")
        refs = [config.providers[name].api_key for name in used]
        if config.proxy.judge_api_key:
            refs.append(config.proxy.judge_api_key)
        judgeless = [name for name in used if not config.providers[name].judge_model]
        if judgeless and not config.proxy.judge_api_key:
            warn(
                f"provider {', '.join(judgeless)} has no judge_model and there is no "
                "[proxy] judge_api_key, so llm graders cannot run on its models"
            )
        # `op whoami` only reflects the current shell's session token; with the desktop-app
        # integration `op read` works without one. Probe the real thing: resolve each reference
        # and throw the value away.
        for ref in [r for r in refs if r.startswith("op://")]:
            if shutil.which("op") is None:
                continue
            argv = ["op", "read", "--no-newline", ref] + (
                ["--account", config.proxy.op_account] if config.proxy.op_account else []
            )
            try:
                probe = subprocess.run(
                    argv, capture_output=True, text=True, timeout=30, check=False
                )
            except subprocess.TimeoutExpired:
                warn(f"{ref}: `op read` timed out (approve the 1Password prompt, or `op signin`)")
                continue
            if probe.returncode == 0 and probe.stdout:
                ok(f"{ref} resolves")
            else:
                why = (probe.stderr.strip().splitlines() or ["no output"])[-1]
                warn(f"{ref}: {why} (unlock the 1Password app, or run `op signin`)")
        for ref in refs:
            if ref.startswith("env:"):
                var = ref.split(":", 1)[1]
                if os.environ.get(var):
                    ok(f"{var} is set")
                else:
                    warn(
                        f"{var} is not set: export it, or point api_key at an op:// or "
                        "keychain: reference in skillbench.toml"
                    )
            elif ref.startswith("op://") and shutil.which("op") is None:
                fail(f"{ref} needs the 1Password CLI (op), which is not on PATH")
            elif ref.startswith("keychain:") and shutil.which("security") is None:
                fail(f"{ref} needs macOS `security`")
            elif not ref.startswith(("op://", "keychain:", "env:")):
                warn("literal api_key in skillbench.toml; prefer op://, keychain: or env:")
    for name, provider in config.providers.items():
        if not provider.builtin and name not in used:
            warn(f"provider {name} is configured but no `{name}/…` model is in `models`")

    for root in config.roots:
        root = root.expanduser()
        if not root.is_dir() or (root / "SKILL.md").is_file():
            continue
        for child in sorted(root.iterdir()):
            if child.name.startswith(".") or child.is_file():
                continue
            if not (child / "SKILL.md").is_file():
                reason = (
                    "broken symlink" if child.is_symlink() and not child.exists() else "no SKILL.md"
                )
                warn(f"{_short(child)}: {reason}; skipped")
    skills = discover(config, include_empty=True)
    with_cases = [s for s in skills if s.cases]
    ok(f"{len(skills)} skill(s) found, {len(with_cases)} with eval cases")
    for s in skills:
        if not s.cases:
            continue
        o = config.overrides(s.name)
        for evals_dir in s.evals:
            for case_dir in list_cases(evals_dir):
                fm = _case_frontmatter(case_dir)
                wants = set(string_list(fm, "allowed_tools") or [])
                gated = sorted(wants & set(GATED_TOOLS) - set(o.allow_tools))
                if gated:
                    fail(
                        f"{s.name}/{case_dir.name}: case asks for {gated} but "
                        f"[skills.{s.name}] allow_tools does not grant them (they'd be removed)"
                    )
                scaffold = scalar(fm, "scaffold_script")
                if scaffold and not o.scaffold:
                    fail(
                        f"{s.name}/{case_dir.name}: has scaffold_script {scaffold!r} but "
                        f"[skills.{s.name}] scaffold = true is not set (fixtures would be missing)"
                    )
                if scaffold and not (case_dir / scaffold).is_file():
                    fail(f"{s.name}/{case_dir.name}: scaffold_script {scaffold!r} is missing")
                if not (case_dir / "graders").is_dir() and "graders:" not in fm:
                    warn(f"{s.name}/{case_dir.name}: no graders; the case cannot score")
                for problem in _case_problems(case_dir):
                    fail(f"{s.name}/{case_dir.name}: {problem}")
        if o.scaffold:
            warn(f"{s.name}: scaffold scripts run unsandboxed as you; keep authoring them yourself")

    print("doctor:", "no problems" if problems == 0 else f"{problems} problem(s)")
    return 1 if problems else 0


# ---------------------------------------------------------------- run


def _max_cost(
    cli: float | None, skill: float | None, default: float | None, *, proxied: bool = False
) -> float | None:
    """CLI wins; zero or negative on the CLI removes the ceiling entirely.

    A proxied run is unpriced: Claude Code estimates cost at Claude prices whatever the
    upstream model, so the ceiling would cut it off at an arbitrary point (a qwen run was
    marked partial at a fictional $10). Proxied runs get no ceiling unless one is asked
    for explicitly on the command line.
    """
    if cli is not None:
        return None if cli <= 0 else cli
    if proxied:
        return None
    return skill if skill is not None else default


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    models = list(args.model) if args.model else list(config.models)
    if not models:
        sys.exit("skillbench: no models given; pass --model or set `models` in skillbench.toml")
    skills = [s for s in _skills_or_exit(config, args.skill) if not config.overrides(s.name).skip]
    if not skills:
        sys.exit("skillbench: every selected skill is marked skip in config")
    matrix = len(models) * len(skills)
    if not args.model and not args.skill and matrix > RUN_GUARD_CELLS and not args.all:
        sys.exit(
            f"skillbench: a bare `run` would cover {matrix} cells ({len(models)} models × "
            f"{len(skills)} skills); narrow it with -m/-s or pass --all to run the whole matrix"
        )

    base_env = child_env(config.unset_env)
    had_error = had_partial = False
    # Models are grouped by provider so one proxy instance serves every model of a provider.
    for provider_name, group in group_by_provider(models, config).items():
        env = base_env
        judge = args.judge_model or config.judge_model
        proxy = None
        if provider_name is not None:
            judge = _provider_judge(config, provider_name, args.judge_model)
            if judge is None:
                had_error = True
                continue
            sources = "; ".join(describe_sources(config, {provider_name: group}))
            if args.dry_run:
                print(f"# {provider_name}: via cliproxyapi on localhost; secrets from {sources}")
            else:
                try:
                    proxy = start_proxy(config, {provider_name: group}, extra_models=(judge,))
                except ProxyError as exc:
                    print(f"  {provider_name}: proxy failed: {exc}")
                    had_error = True
                    continue
                routed = " (judge via Anthropic key)" if proxy.judge_routed else ""
                print(f"→ {provider_name}: cliproxyapi on {proxy.base_url}{routed}", flush=True)
        try:
            for model in group:
                for skill in skills:
                    run_env = env if proxy is None else {**env, **proxy.env(model)}
                    code = _run_one(
                        config, args, skill, model, judge, run_env, provider_name, proxy
                    )
                    had_error = had_error or code == 1
                    had_partial = had_partial or code == 2
        finally:
            if proxy is not None:
                proxy.stop()
    # Exit 1 only for load/launch failures; 2 when every failure was a cost or interrupt cut-off.
    return 1 if had_error else (2 if had_partial else 0)


def _provider_judge(config: Config, provider_name: str, override: str | None) -> str | None:
    """Pick a judge the proxy can actually route; explain when it cannot."""
    judge = override or config.providers[provider_name].judge_model
    if judge is None and config.proxy.judge_api_key:
        judge = config.judge_model
    if judge is None:
        print(
            f"  {provider_name}: no judge. Set providers.{provider_name}.judge_model to a model "
            f"it serves (e.g. {provider_name}/claude-sonnet-4-5) or [proxy] judge_api_key."
        )
        return None
    if split_model(judge, config)[0] is None and not config.proxy.judge_api_key:
        print(
            f"  {provider_name}: judge {judge} would be sent to the proxy, which has no Anthropic "
            f"route; set [proxy] judge_api_key or use {provider_name}/<model>."
        )
        return None
    return judge


def _run_one(
    config: Config,
    args: argparse.Namespace,
    skill: Skill,
    model: str,
    judge: str,
    env: dict[str, str],
    provider_name: str | None,
    proxy,
) -> int:
    """Run one (skill, model) cell. Returns 0, 1 for a launch/load error, 2 for a partial run."""
    o = config.overrides(skill.name)
    spec = RunSpec(
        skill=skill.name,
        model=model,
        judge_model=judge,
        runs=args.runs or o.runs or config.runs,
        ablation=args.ablation or o.ablation or config.ablation,
        concurrency=args.concurrency or config.concurrency,
        max_cost_usd=_max_cost(
            args.max_cost_usd,
            o.max_cost_usd,
            config.max_cost_usd,
            proxied=provider_name is not None,
        ),
        allow_tools=o.allow_tools,
        scaffold=o.scaffold,
        case_glob=args.case,
        tags=tuple(args.tag or ()),
    )
    stamp = timestamp()
    out_dir = run_dir(config.results_dir, model, skill.name, stamp)
    json_path = out_dir / RESULT_FILE
    plugin_dir = config.build_dir / skill.name
    argv = build_argv(spec, plugin_dir, json_path, out_dir, config.claude_bin)

    if args.dry_run:
        print(shlex.join(argv))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    plugin_dir = materialize(skill, config.build_dir)
    evals_hash = evals_fingerprint(plugin_dir)
    print(
        f"→ {skill.name} @ {model}: {len(skill.cases)} case(s) × {spec.runs} run(s), "
        f"ablation {spec.ablation} …",
        flush=True,
    )
    outcome = execute(argv, env, log_path=out_dir / LOG_FILE)
    meta = {
        "skill": skill.name,
        "skill_path": str(skill.path),
        "skill_fingerprint": skill_fingerprint(skill.path),
        "evals_fingerprint": evals_hash,
        "model": model,
        "provider": provider_name,
        "proxy": (
            {
                "binary": config.proxy.binary,
                "base_url": proxy.base_url,
                "judge_via_anthropic_key": proxy.judge_routed,
            }
            if proxy is not None
            else None
        ),
        # Claude Code prices tokens at Claude list rates, so through a proxy the estimate is
        # for the wrong model and means nothing.
        "cost_unreliable": provider_name is not None,
        "judge_model": spec.judge_model,
        "runs": spec.runs,
        "ablation": spec.ablation,
        "allow_tools": list(spec.allow_tools),
        "scaffold": spec.scaffold,
        "case_glob": spec.case_glob,
        "tags": list(spec.tags),
        "argv": argv,
        "returncode": outcome.returncode,
        "started_at": outcome.started_at,
        "duration_seconds": outcome.duration_seconds,
        "skillbench_version": __version__,
    }
    write_meta(out_dir, meta)

    if json_path.is_file():
        result = json.loads(json_path.read_text())
        s = summarize(result)
        flag = " PARTIAL" if s.partial else ""
        delta = "" if s.delta is None else f" Δ{s.delta:+.2f}"
        score = "·" if s.score is None else f"{s.score:.2f}"
        cost = "cost n/a" if provider_name else f"cost ${s.cost_usd:.2f}"
        print(
            f"  {skill.name} @ {model}: score {score}{delta}  {cost}  "
            f"{outcome.duration_seconds:.0f}s  errors {s.errors}{flag}  → {out_dir}"
        )
    else:
        print(f"  {skill.name} @ {model}: FAILED (exit {outcome.returncode}), no result JSON")
        print("    " + outcome.log_tail.replace("\n", "\n    "))
    if outcome.returncode == 1:
        return 1
    return 2 if outcome.returncode else 0


# ---------------------------------------------------------------- report


def cmd_report(config: Config, args: argparse.Namespace) -> int:
    records = load_runs(config.results_dir, skills=args.skill or None)
    if not records:
        print(f"no results under {config.results_dir}")
        return 0
    markdown = args.format == "md"
    newest = latest(records)

    if args.history:
        print(render_history(records, markdown=markdown))
        return 0

    if args.cases:
        models = args.model or sorted({m for (m, _) in newest})
        for skill in args.skill or sorted({s for (_, s) in newest}):
            for model in models:
                record = newest.get((model, skill))
                if record:
                    print(render_cases(record, markdown=markdown))
                    print()
        return 0

    if args.format == "json":
        print(to_json(newest))
        return 0

    seen_models = sorted({m for (m, _) in newest})
    models = args.model or [m for m in config.models if m in seen_models] + [
        m for m in seen_models if m not in config.models
    ]
    skills = args.skill or sorted({s for (_, s) in newest})
    print(render_matrix(newest, models, skills, markdown=markdown))

    if args.against:
        candidates = [m for m in models if m != args.against]
        for candidate in candidates:
            print()
            print(f"{candidate} vs baseline {args.against}")
            diffs = compare(newest, baseline=args.against, candidate=candidate)
            print(render_compare(diffs, min_drop=args.min_drop, markdown=markdown))
    return 0


# ---------------------------------------------------------------- convert


def cmd_convert(config: Config, args: argparse.Namespace) -> int:
    if args.all:
        targets = [
            s
            for s in discover(config, include_empty=True)
            if not s.cases and detect_format(s.path) is not None
        ]
        if not targets:
            print("nothing to convert: every skill with a recognised legacy eval format has cases")
            return 0
        overlay = True  # bulk conversion never writes into skill repos
        pairs = [(s.path, s.name) for s in targets]
    else:
        target = Path(args.skill).expanduser()
        if not target.is_dir():
            try:
                target = discover(config, [args.skill], include_empty=True)[0].path
            except LookupError:
                sys.exit(f"skillbench: {args.skill!r} is neither a directory nor a known skill")
        if detect_format(target) is None:
            sys.exit(f"skillbench: {target} has no evals/evals.json and no evals/*.eval.md")
        overlay = args.overlay
        pairs = [(target, skill_name(target))]

    for path, name in pairs:
        out_dir = Path(args.out).expanduser() if args.out else None
        if overlay:
            out_dir = config.suites_dir / name / "evals"
        result = convert_skill(path, out_dir=out_dir, force=args.force, focus=args.focus)
        where = out_dir or path / "evals"
        print(f"{name}: {len(result.written)} file(s) → {where}")
        for skipped in result.skipped:
            print(f"  skipped {skipped} (exists; use --force to overwrite)")
        for warning in result.warnings:
            print(f"  warning {warning}")
    return 0


# ---------------------------------------------------------------- dashboard / serve


def cmd_dashboard(config: Config, args: argparse.Namespace) -> int:
    out = write_dashboard(config, Path(args.out).expanduser() if args.out else None)
    print(f"wrote {out}")
    if args.open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_serve(config: Config, args: argparse.Namespace) -> int:
    return serve(config, port=args.port, open_browser=args.open)


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillbench",
        description="Evaluate agent skills across Claude models with `claude plugin eval`.",
    )
    parser.add_argument("--config", type=Path, help="path to skillbench.toml (default: nearest)")
    parser.add_argument("--version", action="version", version=f"skillbench {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="show every skill under the roots and its eval coverage")
    p.add_argument("--cases-only", action="store_true", help="only skills that have eval cases")
    p.add_argument(
        "--stale",
        action="store_true",
        help="cells whose skill or graders changed since their newest run, plus the re-run command",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="list case names")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("doctor", help="check claude, auth, roots, and case/config consistency")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("run", help="run eval suites for skills against one or more models")
    p.add_argument("-m", "--model", action="append", help="model id; repeatable (default: config)")
    p.add_argument("-s", "--skill", action="append", help="skill name; repeatable (default: all)")
    p.add_argument("--runs", type=int, help="runs per case per arm")
    p.add_argument("--ablation", choices=("none", "with-without"))
    p.add_argument("--judge-model")
    p.add_argument("-j", "--concurrency", type=int)
    p.add_argument("--max-cost-usd", type=float, help="per (skill, model) ceiling; 0 disables")
    p.add_argument("--case", help="case name glob passed through to claude")
    p.add_argument("--tag", action="append", help="case tag filter; repeatable")
    p.add_argument("--dry-run", action="store_true", help="print the claude commands only")
    p.add_argument(
        "--all",
        action="store_true",
        help="run the whole model × skill matrix even when it is large",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="skill × model matrix, comparisons, history")
    p.add_argument("-m", "--model", action="append", help="restrict/ordered models")
    p.add_argument("-s", "--skill", action="append")
    p.add_argument("--against", metavar="MODEL", help="baseline model to compare others to")
    p.add_argument("--min-drop", type=float, default=0.15, help="regression threshold (0..1)")
    p.add_argument("--cases", action="store_true", help="per-case breakdown of latest runs")
    p.add_argument("--history", action="store_true", help="every stored run, oldest first")
    p.add_argument("--format", choices=("table", "md", "json"), default="table")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("convert", help="evals.json (skill-creator) or *.eval.md → native cases")
    p.add_argument("skill", nargs="?", help="skill name or directory (omit with --all)")
    p.add_argument(
        "--all", action="store_true", help="convert every skill with a legacy format into suites/"
    )
    p.add_argument("--out", help="write cases here instead of <skill>/evals")
    p.add_argument("--overlay", action="store_true", help="write into suites/<skill>/evals")
    p.add_argument("--force", action="store_true", help="overwrite existing case dirs")
    p.add_argument("--focus", choices=("last_message", "trace"), default="trace")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("models", help="list a provider's models and their tool-calling support")
    p.add_argument("provider", help="provider name from [providers.*]")
    p.add_argument("--tools-only", action="store_true", help="hide models without function calling")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("dashboard", help="write a self-contained HTML dashboard of all results")
    p.add_argument("--out", help="output path (default: results/dashboard.html)")
    p.add_argument("--open", action="store_true", help="open it in the browser")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("serve", help="live dashboard on localhost that can also launch runs")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the browser")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        sys.exit(f"skillbench: config error: {exc}")
    return int(args.func(config, args))


if __name__ == "__main__":
    sys.exit(main())
