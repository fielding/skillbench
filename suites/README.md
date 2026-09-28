# Overlay suites

`suites/<skill>/evals/` holds eval cases for a skill whose own directory should not be
edited (third-party, or a checkout without git). Cases here are merged with the skill's
own `evals/` at run time; on a name clash the overlay wins. `skillbench convert <skill>
--overlay` populates a suite from a skill-creator `evals.json` or `*.eval.md` files.
