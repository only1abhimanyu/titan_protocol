"""Prove the LLM branch works without a key, by stubbing the transport.

Three paths matter:
  A. provider configured + model returns good JSON  -> source=llm, personalised
  B. provider configured + model throws             -> source=template, session survives
  C. no provider at all                             -> source=template
"""
import json, os
os.environ.pop("GEMINI_API_KEY", None); os.environ.pop("OPENAI_API_KEY", None)

from engine.llm import LLM
from engine import core, tutor

content = core.Content("content/recursion.json")
state = core.new_state(content)
core.record_answer(content, state, "r07", 1)   # wrong, M_NO_BASE
core.record_answer(content, state, "r08", 1)   # wrong, M_NO_BASE
diag = core.diagnose(content, state)
assert diag and diag["misconception_id"] == "M_NO_BASE", diag
print(f"diagnosis OK: {diag['name']} @ {diag['confidence']} root={diag['root_cause_name']}")

# ---- C: no provider
llm = LLM()
e = tutor.explain(llm, content, state, "base_case", "analogy", diag)
assert e["source"] == "template" and e["body"] and e["worked"], e
print(f"C offline  -> source={e['source']} paras={len(e['body'])} worked={len(e['worked'])}")

# ---- A: provider configured, model answers properly
llm = LLM(); llm.gemini_key = "fake-key-for-test"
def good(system, user):
    assert "base_case" not in user or True
    return json.dumps({
        "headline": "Your base case is a promise you have to keep",
        "hook": "A ladder needs a bottom rung.",
        "body": ["You think recursion stops itself. It does not.",
                 "Every chain of calls needs one that returns without recursing."],
        "worked": ["write fact(n) = n * fact(n-1)", "add if n <= 1: return 1", "trace fact(3) to the bottom"],
        "key_takeaway": "The base case is the only thing that ends the chain."})
llm._raw = good
e = tutor.explain(llm, content, state, "base_case", "analogy", diag)
assert e["source"] == "llm", e["source"]
assert e["headline"].startswith("Your base case"), e["headline"]
print(f"A llm      -> source={e['source']} headline={e['headline']!r}")
e2 = tutor.explain(llm, content, state, "base_case", "analogy", diag)
assert "+cache" in e2["source"], e2["source"]
print(f"A cached   -> source={e2['source']}")

# grading via LLM
def grade_good(system, user):
    return json.dumps({"hits": [{"idea": "base case returns without recursing", "hit": True,
                                "evidence": "returns without calling itself"}],
                       "missing": ["frames are removed LIFO"],
                       "score": 0.75,
                       "feedback": "You have the mechanism. You left out the LIFO ordering."})
llm2 = LLM(); llm2.gemini_key = "fake"
llm2._raw = grade_good
g = tutor.grade(llm2, content, state, "base_case", "the base case returns without calling itself again")
assert g["source"] == "llm" and abs(g["score"] - 0.75) < 1e-9, g
print(f"A grade    -> score={g['score']} feedback={g['feedback'][:44]!r}")

# ---- B: provider configured, model explodes
llm3 = LLM(); llm3.gemini_key = "fake"
def boom(system, user): raise RuntimeError("503 upstream")
llm3._raw = boom
e = tutor.explain(llm3, content, state, "base_case", "worked_example", diag)
assert e["source"] == "template" and e["body"], e
g = tutor.grade(llm3, content, state, "base_case", "base case returns without calling itself")
assert g["source"] == "template", g["source"]
print(f"B failure  -> source={e['source']} (session continues), last_error={llm3.last_error}")

# ---- bad JSON from the model must also fall back, not crash
llm4 = LLM(); llm4.gemini_key = "fake"
llm4._raw = lambda s, u: "Sure! Here is your explanation: it's about recursion."
e = tutor.explain(llm4, content, state, "base_case", "analogy", diag)
assert e["source"] == "template", e["source"]
print(f"B badjson  -> source={e['source']} (no crash)")

print("\nALL LLM-PATH CHECKS PASSED")
