"""Test route.py logic with a fake Anthropic client (no network)."""
import sys, types, json, os, shutil, tempfile

# --- Stub the anthropic module BEFORE importing route.py ---
fake = types.ModuleType("anthropic")

class _Block:
    def __init__(self, text): self.type, self.text = "text", text

class _Usage:
    def __init__(self, i=1200, o=180):
        self.input_tokens, self.output_tokens = i, o

class _Resp:
    def __init__(self, text, stop_reason="end_turn", usage=None):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason
        self.usage = usage or _Usage()

calls = {"haiku_classify": 0, "haiku_brief": 0, "fable_exec": 0,
         "fable_distill": 0, "fable_freeform": 0}

class _Messages:
    queue = []  # push dicts here to script successive Haiku classify answers
    def create(self, **kw):
        assert kw["model"] == "claude-haiku-4-5", kw["model"]
        assert "effort" not in kw.get("output_config", {}), "effort not allowed on Haiku 4.5"
        if "output_config" in kw:                      # classify call (schema output)
            calls["haiku_classify"] += 1
            payload = _Messages.queue.pop(0) if _Messages.queue else {
                "category": "positive", "next_action": "reply", "reason": "asks for pricing"}
            return _Resp(json.dumps(payload))
        calls["haiku_brief"] += 1                      # worker brief call (free text)
        return _Resp("Brief: deal moving, risk on timing, opportunity on expansion.")

class _BetaMessages:
    def create(self, **kw):
        assert kw["model"] == "claude-fable-5", kw["model"]
        assert "thinking" not in kw, "thinking not allowed on Fable"
        assert "server-side-fallback-2026-06-01" in kw.get("betas", []), "fallback missing"
        oc = kw.get("output_config", {})
        if "format" in oc:                              # premium rubric execution
            calls["fable_exec"] += 1
            return _Resp(json.dumps({"category": "objection",
                                     "next_action": "notify_human",
                                     "reason": "escalated premium decision"}))
        if oc.get("effort") == "high":                  # distillation
            calls["fable_distill"] += 1
            return _Resp(json.dumps({
                "task_type": "x",
                "output_schema": {"type": "object",
                                  "properties": {"category": {"type": "string"}},
                                  "required": ["category"], "additionalProperties": False},
                "decision_rules": []}))
        calls["fable_freeform"] += 1                    # analysis / synthesis
        return _Resp("ANALYSIS: prioritized accounts with reasons.",
                     usage=_Usage(i=800, o=300))

class _Beta:
    def __init__(self): self.messages = _BetaMessages()

class Anthropic:
    def __init__(self, *a, **k):
        self.messages, self.beta = _Messages(), _Beta()

fake.Anthropic = Anthropic
sys.modules["anthropic"] = fake

# --- Load route.py now that the stub is in place ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import route

def fresh_rubrics():
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(ROOT, "rubrics", "sales_reply_classification.json"), d)
    return d

results = []

# 1. rulebook exists + benign input -> Haiku answers
d = fresh_rubrics()
out = route.classify("sales_reply_classification", "Can you send pricing?", d)
results.append(("classify: rulebook exists -> Haiku",
                out["model"] == "claude-haiku-4-5"
                and out["route_reason"] == "rubric_available"
                and out["result"]["category"] == "positive"))

# 2. REGRESSION: sensitive keyword -> Fable EXECUTES, user gets an answer,
#    and the saved rulebook is NOT touched (old code used to clobber it)
d = fresh_rubrics()
p = os.path.join(d, "sales_reply_classification.json")
before = open(p).read()
out = route.classify("sales_reply_classification",
                     "We need legal + procurement sign-off first", d)
after = open(p).read()
results.append(("classify: sensitive keyword -> Fable executes, rulebook untouched",
                out["mode"] == "execution"
                and out["model"] == "claude-fable-5"
                and out["route_reason"] == "keyword_escalation"
                and "result" in out
                and before == after))

# 3. no rulebook -> clean error telling you to distill
try:
    route.classify("brand_new_task", "hello", tempfile.mkdtemp())
    clean_error = False
except SystemExit as e:
    clean_error = "distill" in str(e)
results.append(("classify: no rulebook -> explicit error", clean_error))

# 4. distill -> writes the file and reports cost
d = tempfile.mkdtemp()
out = route.distill("lead_scoring", "30 labeled examples...", d)
results.append(("distill: saves rubric + cost log",
                os.path.exists(os.path.join(d, "lead_scoring.json"))
                and out["cost_log"]["usd"] > 0))

# 5. verify mode: two Haiku passes disagree -> Fable decides
d = fresh_rubrics()
_Messages.queue = [
    {"category": "positive", "next_action": "reply", "reason": "a"},
    {"category": "unclear", "next_action": "notify_human", "reason": "b"},
]
out = route.classify("sales_reply_classification", "hmm ok maybe", d, verify=True)
results.append(("classify: haiku disagreement -> Fable decides",
                out["model"] == "claude-fable-5"
                and out["route_reason"] == "haiku_disagreement"))

# 6. analyze SMALL corpus -> direct Fable, zero workers (orchestration would not pay)
n_briefs_before = calls["haiku_brief"]
out = route.analyze("Prioritize these accounts.",
                    {"a.txt": "short thread " * 50, "b.txt": "short thread " * 50})
results.append(("analyze: small corpus -> direct Fable, no workers",
                out["mode"] == "analyze_direct"
                and calls["haiku_brief"] == n_briefs_before
                and "would not pay" in out["route_reason"]))

# 7. analyze LARGE corpus -> Haiku workers + Fable synthesis, compression reported
big = {"t1.txt": "x" * 20000, "t2.txt": "y" * 20000, "t3.txt": "z" * 20000}
out = route.analyze("Prioritize these accounts.", big)
results.append(("analyze: large corpus -> workers + Fable, compression reported",
                out["mode"] == "analyze_orchestrated"
                and calls["haiku_brief"] == n_briefs_before + 3
                and out["compression_x"] > 1
                and out["cost_log"]["total_usd"] > 0))

# 8. analyze --force-direct overrides size routing
out = route.analyze("Prioritize.", big, force="direct")
results.append(("analyze: force-direct override works",
                out["mode"] == "analyze_direct"))

# 9. Fable refusal read BEFORE content (no index crash)
class _RefuseBeta(_BetaMessages):
    def create(self, **kw):
        return _Resp("", stop_reason="refusal")
route.client.beta.messages = _RefuseBeta()
try:
    route.distill("new_type", "x", tempfile.mkdtemp()); refused = False
except SystemExit:
    refused = True
results.append(("distill: Fable refusal handled before content", refused))

print("Calls:", json.dumps(calls), "\n")
ok = True
for name, passed in results:
    print("  [" + ("OK" if passed else "FAIL") + "] " + name)
    ok = ok and passed
print("\n" + ("ALL PATHS PASS" if ok else "FAILED"))
sys.exit(0 if ok else 1)
