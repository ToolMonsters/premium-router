# premium-router

**Fable brain, Haiku price.** A Claude Code Skill that gives you Claude Fable 5
quality while paying close to Claude Haiku 4.5 rates.

One principle drives everything here: **Fable does the judgment, Haiku does the
volume.** Writing good decision rules is hard (Fable's job, paid once). Applying
them is easy (Haiku's job, paid at 1/10th the rate). Reading a mountain of
documents is volume (Haiku workers). Deciding what the mountain means is
judgment (Fable, reading only the compressed briefs).

Everything in this repo was measured live on the API, including the approaches
that FAILED. The anti-patterns section is as important as the features.

## Quickstart (5 minutes)

```bash
# 1. Install
git clone https://github.com/ToolMonsters/premium-router ~/.claude/skills/premium-router
cd ~/.claude/skills/premium-router && pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-key"   # never committed

# 2. Teach it once: Fable writes the rulebook from your labeled examples
python route.py distill --task-type my_task --examples my_examples.txt

# 3. Use it forever: Haiku applies the rulebook, ~10x cheaper
python route.py classify --task-type my_task --input "a new message"

# 4. For document analysis: Fable judges, Haiku workers read when it is big
python route.py analyze --mission "Prioritize these accounts for this week" \
  --docs thread1.txt thread2.txt transcript3.txt
```

The repo ships with a ready-made rulebook (`sales_reply_classification`), so you
can skip step 2 to try it immediately.

## The three commands

| Command | Who thinks | Who does volume | When to use |
|---|---|---|---|
| `distill` | Fable (effort high, once) | nobody | teach a new repeatable task |
| `classify` | Haiku (Fable on escalation) | Haiku | every repeatable request |
| `analyze` | Fable (effort low) | Haiku workers if corpus is large | judgment over documents |

## Measured results (live on the API, not estimates)

### classify: same accuracy as Fable, ~10x cheaper

Benchmark on 15 labeled prospect replies (`examples/bench.py`):

| Method | Accuracy vs labels | Cost / request |
|---|---|---|
| Fable 5 alone | 14 / 15 (93%) | about $0.009 |
| Haiku 4.5 + rulebook | 14 / 15 (93%) | about $0.0013 |

Projected over 10,000 requests: **about $79 with Fable alone vs about $8 with
the skill** (about 90% saved). The two methods even missed one case each, and
not the same one: neither is strictly weaker.

### analyze: pays off only above a compression threshold

We tested Fable-alone vs Fable-with-Haiku-workers on the same 6-account
analysis mission. Both produced excellent, defensible analyses. The economics:

- Small corpus (short threads): briefs compressed only 1.3x, savings a mere 17%.
  Orchestration is NOT worth it below roughly 1.7x compression.
- The compression ratio grows with document length. A 15k-token meeting
  transcript compresses to a ~200-token brief (50x+), which is where the
  4x to 8x reading savings live.

That is why `analyze` routes on corpus size automatically: small corpus goes
straight to Fable at effort low, large corpus gets Haiku workers.

### The effort lever (free savings, straight from Anthropic docs)

Per Anthropic's documentation, Fable 5 at effort low "often exceeds the xhigh
or even max performance of previous models." Thinking tokens bill as output at
$50/M, so effort low is the default here everywhere except distillation, where
effort high is paid once and amortized forever.

## Measured anti-patterns (what NOT to do)

These were all tested live. Learn from our token bill.

1. **Advisor Tool for cost savings.** Haiku executor + Fable advisor sounds
   clever and measured **~19x MORE expensive than Fable alone** ($0.196 vs
   $0.010 per call): the Fable advisor reasons fully on every consultation
   (3300+ output tokens in our test). The Advisor Tool buys quality, never
   savings.
2. **Orchestrating small corpora.** Workers cost more than they save below
   ~1.7x brief compression. Short documents never compress enough.
3. **Prompt caching short rulebooks.** Silently ignored below ~4096 tokens on
   Haiku 4.5. A ~500-token rulebook is already at the cost floor.
4. **Escalating on model-declared confidence.** A small model's "confidence:
   0.72" is not calibrated. Every escalation here is deterministic: keywords,
   schema, corpus size, or disagreement between two independent passes.

## Routing rules (all deterministic)

- rulebook exists and the case is normal: Haiku applies it.
- no rulebook: `classify` refuses with a clear error telling you to run
  `distill`. Distillation is always explicit, never a side effect, and a
  `classify` call can never overwrite a saved rulebook.
- sensitive keyword (`legal`, `procurement`, `enterprise contract`,
  `security`) or `--premium`: Fable applies the same rulebook.
- `--verify`: Haiku runs twice; if the passes disagree, Fable decides.
- `analyze`: corpus under the threshold (default ~6000 tokens) goes straight
  to Fable; above it, one Haiku worker per document briefs it first.

Every response includes a `cost_log` with token counts and estimated USD.

## Install

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-key"
```

Org requirement: Fable 5 requires 30-day data retention. An org on
zero-data-retention gets a 400 on every Fable call.

## Usage

```bash
# Apply an existing rulebook (Haiku, cheap)
python route.py classify --task-type sales_reply_classification \
  --input "Can you send pricing for a team of 12?"

# Force premium execution (Fable applies the rulebook)
python route.py classify --task-type sales_reply_classification \
  --input "..." --premium

# Double-check mode: Haiku twice, Fable arbitrates on disagreement
python route.py classify --task-type sales_reply_classification \
  --input "..." --verify

# Write or rewrite the rulebook from labeled examples (Fable, once)
python route.py distill --task-type sales_reply_classification \
  --examples examples.txt

# Document analysis: routing by corpus size is automatic
python route.py analyze --mission "Top 3 accounts to act on this week and why" \
  --docs meetings/*.txt

# Override the analyze routing if you know better
python route.py analyze --mission "..." --docs big.txt --force-direct
python route.py analyze --mission "..." --docs small.txt --force-workers
```

## Layout

```
premium-router/
  SKILL.md          skill manifest
  route.py          the router (distill / classify / analyze)
  requirements.txt
  LICENSE           MIT
  rubrics/
    sales_reply_classification.json    an example rulebook
  examples/
    bench.py          15-example benchmark, Haiku vs Fable vs labels
    demo_context.py   shows the full context sent to Haiku on each call
    test_route.py     offline logic tests (mocked client, no network)
```

## Test

```bash
# Logic tests, no network and no key (9 paths)
python examples/test_route.py

# Demo: see exactly what Haiku receives and returns (needs a key)
python examples/demo_context.py

# Full Haiku vs Fable benchmark (needs a key)
python examples/bench.py
```

## Honest limits

- The benchmark examples are synthetic. To validate 100% on your data, re-run
  `bench.py` with your own labeled messages.
- `classify` shines on repeatable tasks. `analyze` shines on long documents.
  A one-off question on a short input should just call Fable directly at
  effort low; no architecture beats that.
- Rulebooks can drift: if your inputs change over time, re-run `distill` with
  fresh examples. Nothing detects drift automatically yet.
- The `analyze` token estimate is a chars/4 heuristic, good enough for routing,
  not for billing.

## Models and pricing

| Model | ID | Input / Output (per million tokens) |
|---|---|---|
| Claude Fable 5 | `claude-fable-5` | $10 / $50 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | $1 / $5 |
