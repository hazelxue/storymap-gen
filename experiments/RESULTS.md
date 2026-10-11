# Experiment: does asking first save tokens?

Real model calls (`claude -p`, model claude-sonnet-5, no tools, no user settings or memory). Raw outputs, every prompt
result and every claim check are in `runs/` (`ab_results.json`, `ablation_grounded.json`, `judge_results.json`).
Reproduce: `python run_ab.py --trials 6`, `python analyze.py`, `python judge_runs.py`.

## Setup
Two simulated users with hidden preferences. A spec is **accepted** only if it is valid, meets the persona
(section count, max words per slide, layout, minimum number of claims) and passes the quality gate
(every claim verified against the street data, citations consistent, node graph valid). Chains revise on feedback, up to 4 rounds.

| persona | sections | words/slide | layout | min claims |
|---|---|---|---|---|
| course-report | 3 | <= 25 | docked-sidecar | 2 |
| public-explainer | 5 | <= 45 | longform | 1 |

- **direct**: preferences hidden; the model guesses, then revises on feedback that names exactly what failed (generous to this arm).
- **ask-first**: the four scoping answers are in the first prompt.
- **ask-first + grounded** (ablation): as above, plus the claim verifier's measured street-vs-grid facts in the prompt.

All arms get the same schema, data summary and rules. 12 chains per main arm (6 trials x 2 personas), 10 for the ablation.

## Results
| | direct (n=12) | ask-first (n=12) | ask-first + grounded (n=10) |
|---|---|---|---|
| accepted within 4 rounds | 100% | 100% | 100% |
| accepted on the first draft | 0% | 17% | **100%** |
| mean rounds | 2.0 | 2.0 | **1.0** |
| task tokens per chain (overhead removed) | 17.8k | 12.9k | **7.7k** |
| cost per chain (USD, incl. harness context) | 0.176 | 0.133 | **0.086** |
| chains with a preference violation in round 1 | 12/12 | **0/12** | 0/10 |
| chains with a data-claim error in round 1 | 12/12 | 10/12 | **0/10** |
| claim accuracy in round 1 | 80% (n=125) | 79% (n=82) | **100% (n=92)** |

- direct vs ask-first: cost +$0.043 per chain (95% bootstrap CI 0.018 to 0.071, permutation p=0.002); task tokens ratio 1.38 (p=0.0006).
  The number of rounds did not differ (2.0 vs 2.0, p=1.0).
- Blind LLM judge (24 accepted specs, arm hidden, 4-criterion rubric, 1-5): direct 3.83, ask-first 4.00, p=0.32. No quality loss detected.

## What this shows, and what it does not
1. **Asking removes preference rework completely** (0 of 12 chains vs 12 of 12 violated length, layout or section count).
2. **But asking alone did not reduce the number of rounds.** The bigger source of rework was factual: about 20% of the model's
   claims about the street data were wrong in both arms. The most common error was calling NORTHERN BLVD diagonal to the grid
   (22 failing claims; measured 0.1 deg from the grid axis), the same mistake that was made in the original story by hand.
   JUNCTION BLVD (14 failures) is borderline at 14.5 deg against a 15 deg threshold, so it depends on the threshold; ROOSEVELT AVE (6) is a clear error.
3. **The token saving of ask-first is partly smaller output, not only avoided rework.** Ask-first specs were shorter (6.8 vs 10.4 claims per
   draft, fewer words) because the answers constrain them, and the share of output tokens spent in rejected rounds was not lower (56% vs 46%).
   So "ask first saves tokens" is true here, but the mechanism is not what the pitch says.
4. **Measuring before writing was the largest effect**: with verified facts in the prompt, every chain was accepted on the first draft
   and cost about half of direct generation. This is what `python -m storymap_gen facts` provides, and why the pipeline verifies claims.

## Limits
- Simulated users with preferences I chose; real users give vaguer feedback and have preferences I did not model.
- The ablation facts cover a fixed list of seven streets; it behaves like an answer key for those streets. It was run separately from the
  main experiment (not randomised together) with n=10, so treat its size as indicative. Whether the model stays accurate on streets
  that are not in the facts block was not tested.
- Two personas, one model, one topic and dataset. Model output is non-deterministic; a repeat will differ in the details.
- Cost includes about 35k tokens of fixed Claude Code context per call (mostly cache reads), identical across arms. The
  "task tokens" row subtracts it using a measured baseline (34,982 tokens); dollar figures do not.
- The judge and the writer are the same model family, so absolute scores are optimistic; only the comparison is informative. The grounded arm was not judged.
- Total spend: $5.55 for generation (including a 4-chain pilot that is not in the tables) plus $0.59 for judging.
- Author names in the recorded prompts and model outputs were replaced with the placeholders "Author One; Author Two" before publishing; no numbers were changed.

## Addendum (v0.2): what the new prose-number check finds in the recorded specs
The experiment above used the v0.1 quality gate. v0.2 adds a check that every number written in prose is backed by a verified claim.
`experiments/grounding_audit.py` applies it to the 34 accepted specs without any new model calls (`runs/grounding_audit.json`).

| arm | specs that would now fail | unbacked numbers | of which quote a measured deviation |
|---|---|---|---|
| direct (n=12) | 6 | 10 | 7 |
| ask-first (n=12) | 3 | 3 | 0 |
| ask-first + measured facts (n=10) | 10 | 28 | 23 |

- The old gate let through specs whose text contained numbers nothing verified, in half of the direct specs.
- In the measured-facts arm the flagged numbers are mostly true: the model quoted the measured deviation (for example "37 degrees") that it
  was given, but a spec had no way to declare that number, so it could not be verified. v0.2 adds `stated_deviation_deg` to orientation claims for exactly this.
  Those specs were not regenerated, so the table shows the gap, not a new result.
- Only numbers are checked. A sentence with no number is not.

## Second judge (not completed)
`judge_runs.py` now takes `--model` and `--out` so a second model can re-score the same 24 specs blind and its agreement with the first
judge can be measured. The attempt with a different model failed because the headless `claude -p` login had expired
("OAuth session expired and could not be refreshed"), before any scoring happened. Re-run after logging in again:
`python judge_runs.py --model haiku --out judge_haiku.json`.
