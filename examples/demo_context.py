import json, os, sys, textwrap
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import route

rubric = json.load(open(os.path.join(ROOT, "rubrics", "sales_reply_classification.json")))

# The SYSTEM = the rules (the rulebook). This is what gets re-sent on EVERY call.
rules = ("You classify inbound prospect replies. Apply these rules:\n\n"
         + json.dumps(rubric["decision_rules"], indent=2, ensure_ascii=False)
         + "\n\nEdge cases:\n"
         + json.dumps(rubric["edge_cases"], indent=2, ensure_ascii=False)
         + "\n\nReply ONLY with the JSON: {category, next_action, reason}.")

messages = [
    "Hey, saw your demo. Can you send pricing for a team of 12?",
    "Thanks but we're locked into Salesforce for 2 more years.",
    "let me loop in my manager, she owns this budget",
]

def w(txt, indent="    "):
    return "\n".join(textwrap.fill(l, 92, initial_indent=indent, subsequent_indent=indent)
                      for l in txt.splitlines())

for i, msg in enumerate(messages, 1):
    print("=" * 96)
    print(f"EXAMPLE {i}   : what Haiku receives and returns\n")
    print("[ SYSTEM (the rules, re-sent every time) " + "-"*52)
    print(w(rules))
    print("|")
    print("[ USER (the new message to classify) " + "-"*57)
    print(w(msg))
    print("|")
    result, usage = route.run_haiku(rubric, msg)
    print("=> HAIKU replies " + "-"*77)
    print(w(json.dumps(result, indent=2, ensure_ascii=False)))
    print(f"\n    [tokens: {usage.input_tokens} in / {usage.output_tokens} out]\n")
