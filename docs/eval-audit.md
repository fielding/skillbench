# Eval audit, 2026-09-26

Every case in the author's two skill repos (`<skill>/evals/`), read
against its skill's SKILL.md, cross-checked with the latest Haiku results (runs=1, the
calibration pass of 2026-09-24). This is the record of what each case measures, which ones
are signal, which are guards, and what changed today.

## The four tests a case has to pass

1. **In lane.** The prompt is something a real user would type, the skill is supposed to
   handle it, and the skill actually fires. A case where the skill never loads measures the
   bare model twice.
2. **Planted truth.** The fixture carries specific facts or defects (a hardcoded key, a
   failing test, a ruling attributed to a named reviewer) and the graders check for those
   specifics. "Is the review good" is not a grader; "does the review name the `#[from]`
   on `Transport` and propose `#[source]` + `.map_err`" is.
3. **Skill-shaped.** Every grader traces to a sentence in SKILL.md. If you cannot quote the
   sentence a grader enforces, the grader is measuring the model, not the skill.
4. **Discriminating.** A capable model *without* the skill should fail at least one core
   grader. If both arms pass everything, the case is a regression guard, which is fine as
   long as it is cheap and nobody reads its delta as skill quality.

Grader hygiene underneath those: deterministic graders (`file_exists`, `regex` on a written
file) wherever the skill produces an artifact; `llm` only for judgment, and then **one
assertion per grader**, because a three-vote Haiku judge fails "PASS if ALL of (a)-(e)"
rubrics for the wrong reasons; negative-space guards (`no-push`, `no-edit`,
`never-read-sensitive`) in both arms so a skill that does the wrong thing loses points even
when it also does the right thing.

## Grader classes

Reading the latest Haiku results, every grader falls in one of four classes:

| Class | Meaning | Read it as |
|---|---|---|
| **DISC** | passes with the skill, fails without | the skill's contribution; this is the signal |
| **FLOOR** | passes in both arms | the bare model already does it; a regression guard, contributes nothing to delta |
| **GUARD** | `arm: both` negative-space check that passes in both arms | intended; it exists to catch the skill doing harm |
| **FAILW** | fails with the skill loaded | either a rubric problem (fixed below) or a real skill finding (listed below) |

Before today's edits the census was 113 DISC, 156 FLOOR+GUARD, 76 FAILW. The FAILW group
splits about evenly between "multi-condition rubric" and "the skill really did that".

Two facts about the eval schema that shaped the fixes (from the official plugin-evals docs):

- `arm` accepts only `both` and `with-only`. `with-only` and every `tool_used: Skill` grader
  are **excluded from scoring in both arms** and shown as indicators. There is no way to
  score a grader in one arm only, so "if the review does not mention X, PASS" graders were
  inflating the no-skill score. They now fail when the violation is skipped.
- `tool_used` `input_match` matches the whole JSON tool input, path *and* content. A Write
  guard for `pricing.py` fired on a report that merely mentioned `pricing.py`. Path guards
  are now anchored on `"file_path"\s*:\s*"...`.

## Per-case verdicts

Verdict key: **SIGNAL** = discriminates and is skill-shaped. **PARTIAL** = signal on a subset
of graders, the rest is floor. **GUARD** = cheap regression check, delta expected near zero.
**NOISE** = was measuring nothing until today's change.

### anti-slop
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| dry-run-go-plan | SIGNAL | `--dry-run` contract (write nothing, spawn nothing) plus the Go-stub honesty rule; the plan must name `u02_docs_vs_reality` and severity normalization, which only the skill knows | none |
| quick-reality-check | SIGNAL, expensive | Planted crypto stub, live-looking key, no-op tests; deterministic graders on `.antislop/` files; subagent delegation is the skill's stated contract. Roughly 10 subagents per run | two verdict rubrics split into five one-assertion graders |

### gate (results untrustworthy until the git-in-sandbox fix is applied)
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| floor-finds-planted-failures | PARTIAL | Finding the ruff F401 and the failing test is floor (bare Haiku does it). The skill-shaped part is the preflight probe, the per-stage ledger and stopping after the floor. The prompt is instructive on purpose because the case is scoped to three stages | ledger rubric split in two; `no-source-overwrite` anchored to the path |
| preflight-missing-companions | SIGNAL | Only the skill knows the companion set (`state-space-minimization`, `atomic-changes`...) and the STOP rule. The with-skill failure in calibration (no probe run, `rust-conventions` reported as missing for a Python repo) is a real finding to confirm at runs=3 | stop rubric reduced to its one assertion |

### handoff
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| catch-me-up-read | GUARD | Bare Haiku finds and reads `.handoff/` on its own; one grader of nine discriminated. Cheap and read-only, keep it. To get lift, plant a decoy (a root `NOTES.md` that contradicts STATUS) | summary rubric split in three |
| power-out-write | SIGNAL | Deterministic file graders, secret canary from `scripts/smoke.sh`, planted half-finished diff. STATUS claiming the tests pass is the real finding. Needs git in the sandbox | STATUS and NEXT rubrics split into five |

### intent (git-dependent)
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| backoff-with-stated-why | SIGNAL | What must come from the diff, Why/Decision from the prompt, key redacted | seven-condition content rubric split in three; path guards anchored (the old one tripped on intent.md mentioning `http_client.py`) |
| csv-change-missing-why | SIGNAL | Asks for the Why instead of inventing one; `ends-with-question` and `partial-file-honest` both discriminate | what-from-diff split in two; guards anchored |

### retro (git-dependent)
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| detached-session-no-config | SIGNAL | Report-only skill half, no invented tracker, note under `.handoff/retro/`. Trace finding: with the skill, Haiku wrote domain learnings into Claude Code auto-memory; the `writes-stay-in-handoff` guard caught it and stays strict | two rubrics split into six |
| recurrence-and-conflict | SIGNAL | Seen-counter bump, conflict flagged not overwritten, original entry preserved on disk. The `seen-counter-bumped` regex uses a 600-character window and is the brittle one | recurrence rubric split in two |

### rust-conventions
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| review-inbox-module | SIGNAL | Six planted violations, each with the convention's fix direction | the six `fix-*` graders no longer pass when the violation is skipped |
| write-config-loader | SIGNAL | `#[source]`, `.map_err`, error shape and hygiene discriminate; the four `no-*` regexes are floor on a small loader | none |

### scrub-ai-tells
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| detect-only-report | was NOISE | The skill never fired on "do a scrub pass... detect only", so both arms were the bare model | prompt starts with `/scrub-ai-tells`; findings rubric split in three |
| scrub-commit-message | GUARD + finding | Bare Haiku removes em dashes and filler from a short message; the one discriminating grader went negative (skill arm flattened the change narration or added commentary) | rubric split in three so the cause is visible |
| scrub-draft-in-place | SIGNAL | Deterministic graders on the edited file; the `--verbose` double-hyphen trap; an em dash survived with the skill loaded, which is a finding | meaning rubric split in two |

### skill-distill and agents-distill (prompts start with the slash command; `disable-model-invocation`)
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| consent-gate-lists-files (both skills) | SIGNAL | The canonical negative-space case: a canary token in the transcripts, `no-transcript-*` guards, and the baseline read the files | none |
| preapproved-finds-release-flow | SIGNAL | Planted release FLOW and worktree HOW with known counts | five-condition rubric split into five |
| preapproved-routes-tiers | SIGNAL | Planted GLOBAL / LANGUAGE / PROJECT directives plus a procedural decoy | six-condition rubric split into six |

### tix
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| track-three-tasks | SIGNAL | The canonical planted-artifact case: three issues, one `blocks` dep in the right direction, checked in `.tix/issues.jsonl` | none |
| what-next-ready | GUARD | Bare Haiku reasons out the block from `tix list`; only `tix ready` usage discriminates. To get lift: eight issues with a dep chain so priority-first goes wrong | none |

### tutor
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| buggy-cache-understand | SIGNAL | Checklist written, orients first; teaching-not-fixing is floor when the prompt says "teach me" | none |
| auth-flow-walkthrough | SIGNAL | Orient turn on a large ask; checklist scoped to auth.py branches | checklist rubric split in two |
| rust-ownership-concept | SIGNAL | Python-anchored calibration is the skill's rule and Haiku+skill asked generic questions instead: a finding | checklist rubric split in two |

### typescript-conventions
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| review-webhook-module | SIGNAL | Six planted violations | eight `fix-*` graders no longer pass when skipped |
| write-webhook-handler | SIGNAL | Fail-closed, timingSafeEqual, UNIQUE + insert-catch, dedupe first | fail-policy and idempotency rubrics split into six |

### ui-style-neobrutalist
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| button-and-card-css | SIGNAL | Six regexes discriminate (hard shadow, no blur, lift/sink, focus); radius 0 and no gradient are floor | none |
| review-soft-styles | PARTIAL | Eleven planted violations is too easy: bare Haiku names seven. The fix-direction graders carry the signal. To sharpen: four or five subtle violations | four `fix-*` graders no longer pass when skipped |

### vault-lint
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| coordinated-vault-log-report | SIGNAL | Truncated log line, protocol violation, sync-conflict copy, two frontmatter drifts; append-only rule checked on disk | log rubric split in three; `no-edit-log` anchored to the path |
| normalize-solo-vault | SIGNAL + finding | With the skill loaded Haiku made zero Bash calls in three turns and normalized nothing; the bare model hand-edited and passed. Likely cause: SKILL.md gives the bundled script path as `scripts/normalize_frontmatter.py` with no skill-dir anchor | none |

### vault-triage
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| inbox-and-capture-residue | SIGNAL | Four Inbox items with one ambiguous, a floating capture, a missing index, a stale link; Bash withheld so the host Obsidian CLI cannot answer for the wrong vault | routing rubric split into five |
| thin-config-fallback | was NOISE | Never fired on "Vault triage, please" | prompt starts with `/vault-triage` |

### blog-scout
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| judgment-missing-ledger | SIGNAL, negative | The skill hardcodes `~/notes`; pointed at `./vault`, Haiku+skill bailed without naming the win while the bare model named it. A finding about the skill, not the case | none |
| update-existing-ledger | GUARD + finding | Bare Haiku updates the entry in place fine; `dgrimes`/`inkwell` landed in the ledger in both arms, against the privacy rule | none |

### blog-sweep
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| empty-sandbox-reports-missing-sources | PARTIAL | Only the honest-degradation path is testable in a sandbox; the happy path needs a real vault, session history and Slack | rubric split in three; `names-vault-path` now requires `~/notes`, not the word "notes" |

### my-voice
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| corporate-slack-to-voice | SIGNAL, negative | Em dashes appear with the skill loaded and the 14-jobs fact is dropped; consistent across two runs | none |
| readme-intro-in-voice | SIGNAL, negative | Same pattern; first person and honest hedge also fail with the skill | none |

Design note: the skill's mandatory revision pass is performed on a drafted file. These cases
run it in chat with no Write tool, which is how it is usually used. A second, file-mode
variant (Write + Edit granted, graders reading the file) would be a fair complement.

### review-crew
| Case | Verdict | Why | Changed today |
|---|---|---|---|
| pr-artifacts-and-failure-modes | SIGNAL | Reference-doc recall: history path, `pr-comment.md`, stranding, NO_CONSENSUS; all seven discriminate | two rubrics split into three |
| unpushed-branch-refuses | SIGNAL (git-dependent) | `git remote -v` fails in the sandbox, so Haiku could not verify push state and tried to launch the crew | none |

## Real skill findings (not grader problems)

Each of these came from a trace, not a rubric. Confirm at runs=3 before editing a skill.

- **retro** routes domain learnings into Claude Code auto-memory instead of the project retro
  note. The skill mentions auto-memory only for user preferences.
- **vault-lint** gives its bundled script paths relative to nothing. One run: zero Bash calls,
  nothing normalized. anti-slop solves the same problem with a skill-dir discovery snippet.
- **blog-scout** hardcodes `~/notes`. A prompt-supplied vault path is enough to make the skill
  arm score below the baseline. handoff's `vault_root` resolution is the model to copy.
- **my-voice** makes Haiku use em dashes and drop facts. Two runs, both cases.
- **blog-scout** privacy: a friend's handle landed in the ledger in both arms.
- **gate** preflight on Haiku skipped the probe and reported `rust-conventions` missing for a
  Python repo (the probe script's default `pack=rust-conventions`).
- **scrub-ai-tells** left an em dash in `draft.md` and, on the commit message, the discriminating
  grader went negative.
- **tutor** on the Rust-for-Python-dev case asked generic questions instead of anchoring to Python.
- **anti-slop --quick** on the first run did the review inline instead of delegating.
- **review-crew** tried to launch the crew on an unpushed branch (needs the git fix to confirm).

## Cases still blocked by the sandbox git regression

gate (both), handoff/power-out-write, intent (both), retro (both), review-crew/unpushed-branch-refuses.
Fix on the host, then re-run those five suites:

```sh
ln -s /Library/Developer/CommandLineTools/usr/bin/git /opt/homebrew/bin/git
```

## What to run after these edits

The suites edited today need a fresh run before their numbers mean anything again, because the
store fingerprints the `evals/` directory and the dashboard's history view marks the break.
Everything else in the current runs=3 matrix stands.
