import os, json, argparse
from anthropic import Anthropic

client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment

ESCALATION_KEYWORDS = ("legal", "procurement", "enterprise contract", "security", "soc 2")

RATE = {"claude-fable-5": (10, 50), "claude-haiku-4-5": (1, 5)}  # $/M tokens (in, out)

# Below roughly this corpus size, orchestration does not pay: briefs barely
# compress short documents (measured breakeven is ~1.7x compression).
DEFAULT_ANALYZE_THRESHOLD = 6000


def usd(model, usage):
    ri, ro = RATE[model]
    return round(usage.input_tokens * ri / 1e6 + usage.output_tokens * ro / 1e6, 6)


def est_tokens(text):
    return max(1, len(text) // 4)  # rough chars/4 heuristic, good enough for routing


def extract_json(text):
    """Fable often wraps its JSON in prose/markdown. Pull it out cleanly."""
    t = text.strip()
    if "```" in t:
        t = t.split("```", 2)[1]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1])


_UNSUPPORTED = ("minimum", "maximum", "multipleOf", "minLength", "maxLength",
                "minItems", "maxItems", "pattern", "$schema")

def normalize_schema(node):
    """Harden a distilled output_schema: strip constraints that structured
    outputs reject, and force additionalProperties:false + required everywhere."""
    if isinstance(node, dict):
        for k in _UNSUPPORTED:
            node.pop(k, None)
        if node.get("type") == "object" or "properties" in node:
            node.setdefault("type", "object")
            node["additionalProperties"] = False
            if "properties" in node:
                node["required"] = list(node["properties"].keys())
        for v in node.values():
            normalize_schema(v)
    elif isinstance(node, list):
        for v in node:
            normalize_schema(v)
    return node


def rubric_path(task_type, rubrics_dir):
    return os.path.join(rubrics_dir, task_type + ".json")


def load_rubric(task_type, rubrics_dir):
    p = rubric_path(task_type, rubrics_dir)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------- model calls

def run_haiku(rubric, text):
    """Cheap execution: Haiku applies the rubric (the high-volume mode)."""
    # The rubric is identical on every call, so we mark it for caching.
    # (Caching only kicks in above ~4096 tokens on Haiku 4.5; below that it is
    #  silently ignored, no error.)
    system_blocks = [{
        "type": "text",
        "text": "Apply this rubric strictly:\n" + json.dumps(rubric, ensure_ascii=False),
        "cache_control": {"type": "ephemeral"},
    }]
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        # No output_config.effort here -> 400 on Haiku 4.5
        system=system_blocks,
        messages=[{"role": "user", "content": text}],
        output_config={"format": {"type": "json_schema", "schema": rubric["output_schema"]}},
    )
    out = next(b.text for b in resp.content if b.type == "text")
    return json.loads(out), resp.usage


def run_fable(rubric, text):
    """Premium execution: Fable applies the SAME rubric (escalated cases)."""
    resp = client.beta.messages.create(
        model="claude-fable-5",
        max_tokens=1000,
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": rubric["output_schema"]}},
        betas=["server-side-fallback-2026-06-01"],  # refusal safety net, opt-in
        fallbacks=[{"model": "claude-opus-4-8"}],
        system="Apply this rubric strictly:\n" + json.dumps(rubric, ensure_ascii=False),
        messages=[{"role": "user", "content": text}],
    )
    if resp.stop_reason == "refusal":  # check BEFORE reading content
        raise SystemExit("Fable refused the request.")
    out = next(b.text for b in resp.content if b.type == "text")
    return json.loads(out), resp.usage


def fable_freeform(system, user_text, effort="low", max_tokens=2000):
    """Free-form Fable call (analysis, synthesis). Default effort is low: per
    Anthropic's docs, low-effort Fable often exceeds the max performance of
    previous frontier models, at a fraction of the thinking spend."""
    resp = client.beta.messages.create(
        model="claude-fable-5",
        max_tokens=max_tokens,
        # No thinking field (always on for Fable) -> otherwise 400
        output_config={"effort": effort},
        betas=["server-side-fallback-2026-06-01"],
        fallbacks=[{"model": "claude-opus-4-8"}],
        system=system,
        messages=[{"role": "user", "content": user_text}],
    )
    if resp.stop_reason == "refusal":
        raise SystemExit("Fable refused the request.")
    return " ".join(b.text for b in resp.content if b.type == "text"), resp.usage


def haiku_brief(name, text, brief_words=120):
    """Worker: Haiku reads one document and compresses it into a short brief."""
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=400,
        system=("You are an analyst preparing a brief for an executive. Summarize the "
                "document in max " + str(brief_words) + " words: status, sentiment, "
                "key facts (numbers, names, dates), main risk, main opportunity, "
                "deadlines. Keep decisive verbatim quotes. No fluff."),
        messages=[{"role": "user", "content": "Document: " + name + "\n\n" + text}],
    )
    return " ".join(b.text for b in resp.content if b.type == "text"), resp.usage


def distill_fable(examples):
    """Distillation: Fable writes the rubric, once. effort=high is worth it here
    because this cost is paid a single time and amortized over every request."""
    resp = client.beta.messages.create(
        model="claude-fable-5",
        max_tokens=8000,
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-06-01"],
        fallbacks=[{"model": "claude-opus-4-8"}],
        system="Produce ONE reusable rubric (rules + edge cases + output schema) "
        "that lets a small model handle this family of tasks. "
        "Reply in JSON: {task_type, output_schema, decision_rules, edge_cases}.",
        messages=[{"role": "user", "content": "Examples and corrections:\n" + examples}],
    )
    if resp.stop_reason == "refusal":
        raise SystemExit("Fable refused the distillation.")
    rubric = extract_json(next(b.text for b in resp.content if b.type == "text"))
    if "output_schema" in rubric:
        rubric["output_schema"] = normalize_schema(rubric["output_schema"])
    return rubric, resp.usage


# ---------------------------------------------------------------- commands

def classify(task_type, text, rubrics_dir="rubrics", premium=False, verify=False):
    """Route one request through the rulebook. Never touches the saved rubric."""
    rubric = load_rubric(task_type, rubrics_dir)
    if rubric is None:
        raise SystemExit(
            "No rulebook for '" + task_type + "'. Create one first with your labeled examples:\n"
            "  python route.py distill --task-type " + task_type + " --examples <text or file>")

    sensitive = any(k in text.lower() for k in ESCALATION_KEYWORDS)
    if premium or sensitive:
        result, usage = run_fable(rubric, text)
        return {"mode": "execution", "model": "claude-fable-5",
                "route_reason": "forced_premium" if premium else "keyword_escalation",
                "result": result,
                "cost_log": {"in": usage.input_tokens, "out": usage.output_tokens,
                             "usd": usd("claude-fable-5", usage)}}

    result, usage = run_haiku(rubric, text)
    spent = usd("claude-haiku-4-5", usage)
    reason, model = "rubric_available", "claude-haiku-4-5"

    if verify:
        # Second independent Haiku pass. Disagreement is a REAL, deterministic
        # escalation signal (unlike a self-declared confidence score).
        result2, usage2 = run_haiku(rubric, text)
        spent += usd("claude-haiku-4-5", usage2)
        if (result.get("category"), result.get("next_action")) != \
           (result2.get("category"), result2.get("next_action")):
            result, fu = run_fable(rubric, text)
            spent += usd("claude-fable-5", fu)
            reason, model = "haiku_disagreement", "claude-fable-5"
        else:
            reason = "rubric_available_verified"

    return {"mode": "execution", "model": model, "route_reason": reason,
            "result": result,
            "cost_log": {"in": usage.input_tokens, "out": usage.output_tokens,
                         "usd": round(spent, 6)}}


def distill(task_type, examples, rubrics_dir="rubrics"):
    """Have Fable write (or rewrite) the rulebook. Explicit, never implicit."""
    rubric, usage = distill_fable(examples)
    os.makedirs(rubrics_dir, exist_ok=True)
    p = rubric_path(task_type, rubrics_dir)
    with open(p, "w") as f:
        json.dump(rubric, f, indent=2)
    return {"mode": "distillation", "model": "claude-fable-5", "rubric_saved": p,
            "cost_log": {"in": usage.input_tokens, "out": usage.output_tokens,
                         "usd": usd("claude-fable-5", usage)}}


def analyze(mission, docs, threshold=DEFAULT_ANALYZE_THRESHOLD, force=None):
    """Fable's judgment on a document corpus. The routing question is only WHO
    does the reading:
      - small corpus: Fable reads it directly (orchestration measured NOT to
        pay below ~1.7x brief compression, which short docs never reach)
      - large corpus: Haiku workers read and compress each document into a
        brief; Fable reads only the briefs and decides
    docs: dict name -> text."""
    corpus_tokens = sum(est_tokens(t) for t in docs.values())
    use_workers = force == "workers" or (force is None and corpus_tokens >= threshold)

    if not use_workers:
        corpus = "\n\n".join("=== " + k + " ===\n" + v for k, v in docs.items())
        out, u = fable_freeform("You are a senior analyst.", mission + "\n\n" + corpus)
        return {"mode": "analyze_direct", "model": "claude-fable-5",
                "route_reason": ("corpus ~" + str(corpus_tokens) + " tok < " +
                                 str(threshold) + ": orchestration would not pay"),
                "result": out,
                "cost_log": {"in": u.input_tokens, "out": u.output_tokens,
                             "usd": usd("claude-fable-5", u)}}

    briefs, w_cost = [], 0.0
    for k, v in docs.items():
        b, u = haiku_brief(k, v)
        briefs.append("=== BRIEF: " + k + " ===\n" + b)
        w_cost += usd("claude-haiku-4-5", u)
    out, fu = fable_freeform(
        "You are a senior analyst. Your team prepared these briefs.",
        mission + "\n\n" + "\n\n".join(briefs))
    f_cost = usd("claude-fable-5", fu)
    return {"mode": "analyze_orchestrated",
            "model": "claude-fable-5 orchestrator + " + str(len(docs)) + " claude-haiku-4-5 workers",
            "route_reason": ("corpus ~" + str(corpus_tokens) + " tok >= " +
                             str(threshold) + ": haiku workers read, fable decides"),
            "compression_x": round(corpus_tokens / max(fu.input_tokens, 1), 1),
            "result": out,
            "cost_log": {"workers_usd": round(w_cost, 6), "fable_usd": f_cost,
                         "total_usd": round(w_cost + f_cost, 6)}}


def main():
    p = argparse.ArgumentParser(description="Fable brain, Haiku price.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify", help="apply an existing rulebook to one input")
    c.add_argument("--task-type", required=True)
    c.add_argument("--input", required=True, help="text or file path")
    c.add_argument("--premium", action="store_true", help="force Fable execution")
    c.add_argument("--verify", action="store_true",
                   help="run Haiku twice; escalate to Fable on disagreement")
    c.add_argument("--rubrics-dir", default="rubrics")

    d = sub.add_parser("distill", help="have Fable write the rulebook from examples")
    d.add_argument("--task-type", required=True)
    d.add_argument("--examples", required=True, help="labeled examples: text or file path")
    d.add_argument("--rubrics-dir", default="rubrics")

    z = sub.add_parser("analyze", help="Fable analyzes documents; Haiku workers "
                                       "do the reading when the corpus is large")
    z.add_argument("--mission", required=True, help="what you want decided/produced")
    z.add_argument("--docs", required=True, nargs="+", help="one or more file paths")
    z.add_argument("--threshold", type=int, default=DEFAULT_ANALYZE_THRESHOLD,
                   help="corpus tokens above which workers kick in")
    g = z.add_mutually_exclusive_group()
    g.add_argument("--force-workers", action="store_true")
    g.add_argument("--force-direct", action="store_true")

    a = p.parse_args()
    if a.cmd == "classify":
        text = open(a.input).read() if os.path.exists(a.input) else a.input
        print(json.dumps(classify(a.task_type, text, a.rubrics_dir,
                                  a.premium, a.verify), indent=2))
    elif a.cmd == "distill":
        ex = open(a.examples).read() if os.path.exists(a.examples) else a.examples
        print(json.dumps(distill(a.task_type, ex, a.rubrics_dir), indent=2))
    else:
        docs = {os.path.basename(f): open(f).read() for f in a.docs}
        force = "workers" if a.force_workers else ("direct" if a.force_direct else None)
        print(json.dumps(analyze(a.mission, docs, a.threshold, force), indent=2))


if __name__ == "__main__":
    main()
