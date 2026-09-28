# AGENTS.md: working on skillbench

skillbench is a thin, dependency-free Python layer over `claude plugin eval`. It
exists to answer one question repeatedly: when a new Claude model ships, which of
my skills got better, which got worse, and by how much.

- **Wrap, never reimplement.** `claude plugin eval` owns sandboxing, grading, and
  the HTML report. skillbench only discovers skills, builds the wrapper plugin,
  composes the command, stores results append-only, and renders comparisons.
- **Stdlib only at runtime.** `tomllib`, `json`, `subprocess`, `pathlib`. Dev tools
  (`pytest`, `ruff`) live in the `dev` extra.
- **Results are data, not artifacts.** `results/` is append-only and gitignored here
  (transcripts of your own skills are yours to keep private); never rewrite a past
  run, add a new one. Every run stores `meta.json` with the skill's content
  fingerprint so a score change can be attributed to a skill edit vs. a model change.
- **Skill repos are read-only to this tool.** Runs copy skills into `build/`.
- **Non-Claude models go through a run-scoped CLIProxyAPI** (`proxy.py`). Never write an
  upstream key anywhere but the 0600 config that lives for the run; never log it; resolve
  it from `op://`, `keychain:` or `env:` references (`secrets.py`). The proxy binds to
  localhost with a random per-run token and the management API off.
- **Check before you run.** `uv run skillbench doctor` catches the two silent
  failure modes: cases asking for Write/Edit/Bash without a config grant, and
  scaffold scripts without `scaffold = true`.
- Lint and test: `uv run ruff check . && uv run pytest`.
- The author tracks issues in a local `.tix/` (gitignored); use GitHub issues for the public repo.
