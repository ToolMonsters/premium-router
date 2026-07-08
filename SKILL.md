---
name: premium-router
description: Use Claude Fable 5 intelligence at close to Claude Haiku 4.5 cost. Three commands. distill has Fable write a reusable rulebook once. classify has Haiku apply it on volume, with deterministic escalation back to Fable. analyze has Fable judge a document corpus, with Haiku workers doing the reading when the corpus is large. Trigger on "classify this prospect reply", "qualify this lead", "distill a rubric for X", "analyze these threads/transcripts".
---

# Premium Router v2: Fable brain, Haiku price

Three commands, one principle: Fable does the JUDGMENT, Haiku does the VOLUME.
Every routing decision is deterministic and every response carries a cost_log
with token counts and estimated USD.

## classify (Haiku, cheap, the high-volume mode)
Applies an existing rulebook to one input.

```
python route.py classify --task-type <type> --input "<text or file path>"
```

Flags:
- `--premium` forces Fable to apply the rulebook (premium execution).
- `--verify` runs Haiku twice; if the two passes disagree, Fable decides.

## distill (Fable, expensive, run once per task)
Has Fable write (or rewrite) the rulebook from your labeled examples.
Uses effort high: this cost is paid once and amortized over every request.

```
python route.py distill --task-type <type> --examples "<text or file path>"
```

## analyze (Fable judgment over documents, Haiku does the reading at scale)
Give a mission and one or more document files. Routing is automatic:
- corpus under ~6000 tokens: Fable reads it directly at effort low
  (orchestration measured NOT to pay on small corpora)
- corpus above the threshold: one Haiku worker per document compresses it
  into a brief; Fable reads only the briefs and produces the analysis

```
python route.py analyze --mission "Prioritize these accounts for this week" \
  --docs thread1.txt thread2.txt transcript3.txt
```

Flags: `--threshold N`, `--force-workers`, `--force-direct`.

## Routing rules (deterministic, coded in route.py)
- Rulebook exists + normal case: Haiku applies it.
- No rulebook: classify refuses with a clear error. Distillation is always an
  explicit step, never a side effect. A classify call can never overwrite a
  saved rulebook.
- Sensitive keyword (legal, procurement, enterprise contract, security) or
  `--premium`: Fable applies the same rulebook.
- `--verify` disagreement between two Haiku passes: Fable decides.
- analyze routes on measured corpus size, never on model self-assessment.
- Never escalate on a model-declared "confidence" score.

## Measured anti-patterns (do NOT do these)
- Advisor Tool (Haiku executor + Fable advisor): measured ~19x MORE expensive
  than Fable alone. It boosts quality, it does not save money.
- Orchestrating small corpora: briefs barely compress short documents; below
  ~1.7x compression the workers cost more than they save.
- Prompt caching short rulebooks: ignored below ~4096 tokens on Haiku 4.5.

## Requirements
- `ANTHROPIC_API_KEY` in the environment.
- The `anthropic` package installed (see requirements.txt).
- An org that is NOT on zero-data-retention (Fable returns 400 otherwise).

## Pricing context
Fable 5: $10 / $50 per million tokens (in / out). Haiku 4.5: $1 / $5.
Fable at effort low often exceeds the max performance of previous frontier
models (per Anthropic docs), so effort low is the default everywhere except
distillation.
