import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import route

rubric = json.load(open(os.path.join(ROOT, "rubrics", "sales_reply_classification.json")))
SCHEMA = rubric["output_schema"]

# 15 realistic examples + the "right answer" (label = what you would put)
DATA = [
    ("Can you send pricing for a team of 12?", "positive"),
    ("We're locked into Salesforce for 2 more years.", "objection"),
    ("let me loop in my manager, she owns this budget", "referral"),
    ("ok", "unclear"),
    ("Not interested, please remove me from your list.", "not_interested"),
    ("This looks amazing, can we book a demo Thursday?", "positive"),
    ("Maybe next quarter, no bandwidth right now.", "objection"),
    ("Our COO handles this, cc'ing him now.", "referral"),
    ("thanks", "unclear"),
    ("We already use a competitor and we're happy.", "not_interested"),
    ("Send me the pricing sheet and a couple case studies.", "positive"),
    ("Budget is frozen until the new fiscal year.", "objection"),
    ("I'm leaving the company, talk to Priya instead.", "referral"),
    ("not for us", "not_interested"),
    ("Interesting. What's your pricing model?", "positive"),
]

RATE = {"claude-fable-5": (10, 50), "claude-haiku-4-5": (1, 5)}
def cost(m, u): ri, ro = RATE[m]; return u.input_tokens*ri/1e6 + u.output_tokens*ro/1e6

def fable_direct(text):
    r = route.client.beta.messages.create(
        model="claude-fable-5", max_tokens=400,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}],
        system="Classify this inbound prospect reply. Return the requested JSON.",
        messages=[{"role": "user", "content": text}])
    return json.loads(next(b.text for b in r.content if b.type=="text")), r.usage

h_ok = f_ok = 0
h_cost = f_cost = 0.0
cache_created = cache_read = 0
print(f"{'#':>2}  {'message':42}  {'expected':15}  {'HAIKU':15}  {'FABLE':15}")
print("-"*100)
for i, (msg, label) in enumerate(DATA, 1):
    h_out, h_u = route.run_haiku(rubric, msg)
    f_out, f_u = fable_direct(msg)
    h_cost += cost("claude-haiku-4-5", h_u); f_cost += cost("claude-fable-5", f_u)
    cache_created += getattr(h_u, "cache_creation_input_tokens", 0) or 0
    cache_read += getattr(h_u, "cache_read_input_tokens", 0) or 0
    hc, fc = h_out.get("category"), f_out.get("category")
    h_ok += (hc == label); f_ok += (fc == label)
    mk = lambda v, lab: (v or "?") + (" ok" if v==lab else " X")
    print(f"{i:>2}  {msg[:42]:42}  {label:15}  {mk(hc,label):15}  {mk(fc,label):15}")

n = len(DATA)
print("-"*100)
print(f"\nAccuracy vs your labels :  HAIKU+rulebook {h_ok}/{n} ({100*h_ok//n}%)   "
      f"FABLE alone {f_ok}/{n} ({100*f_ok//n}%)")
print(f"Cost of {n} calls       :  HAIKU ${h_cost:.4f}   FABLE ${f_cost:.4f}   "
      f"-> {f_cost/h_cost:.1f}x cheaper")
print(f"Haiku cache (rulebook)  :  created {cache_created} tok, read {cache_read} tok  "
      f"{'(cache active)' if cache_read else '(rulebook too short -> cache ignored, expected)'}")
print(f"\nProjection 10,000 calls :  FABLE ${f_cost/n*10000:.0f}   "
      f"SKILL ${h_cost/n*10000:.0f}  (+ 1 distillation ~$0.05)")
