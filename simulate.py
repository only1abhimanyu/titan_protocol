"""Headless end-to-end proof that the loop is stateful and self-correcting.

  python simulate.py recursion 30 learner    # a student who actually learns
  python simulate.py recursion 30 struggler  # a student stuck on two beliefs
"""
import sys
from engine.llm import LLM
from engine.session import Registry

TARGET = sys.argv[1] if len(sys.argv) > 1 else "recursion"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 30
MODE = sys.argv[3] if len(sys.argv) > 3 else "learner"

reg = Registry(LLM())
content = reg.topics[TARGET]
WEAK_TAGS = {"M_NO_BASE", "M_RETURN_ORDER"}


def choose(item):
    opts = content.items[item["id"]]["options"]
    if MODE == "struggler":
        for i, o in enumerate(opts):
            if o["misconception"] in WEAK_TAGS:
                return i
    return next(i for i, o in enumerate(opts) if o["correct"])


def teachback_for(cid):
    """A student who genuinely understood writes something like this."""
    c = content.concepts[cid]
    return (f"{c['name']}: {c['summary']} Concretely, {c['key_ideas'][0]}, and "
            f"{c['key_ideas'][1]}. The thing I had backwards was that "
            f"{c['key_ideas'][-1]}.")


resp = reg.create(TARGET, seed=7)
sid = resp["session_id"]
print(f"\n=== {TARGET} / {MODE} | session {sid} ===\n")

for n in range(N):
    step, st = resp["step"], resp["state"]
    read = f"readiness {st['metrics']['readiness']:.2f}"
    t = step["type"]

    if t == "quiz":
        item = step["item"]
        resp = reg.answer(sid, item["id"], choose(item))
        fb = resp["feedback"]
        mark = "OK  " if fb["correct"] else "MISS"
        extra = ""
        if fb["propagated"]:
            extra = " | prop: " + ", ".join(
                f"{p['concept']}{p['delta']:+.2f}" for p in fb["propagated"])
        print(f"[{n:02d}] {mark} {item['id']} ({step['concept_name']}) "
              f"{fb['mastery_before']:.2f}->{fb['mastery_after']:.2f} | {read}{extra}")
        if fb["misconception"]:
            print(f"      tagged: {fb['misconception']['name']}")

    elif t == "teach":
        d = step.get("diagnosis")
        why = f"[{step['reason']}"
        if d:
            why += f" | dx: {d['name']} {d['confidence']:.0%} ({d['evidence_count']} items)"
        why += f" | modality: {step['explanation'].get('modality')}]"
        print(f"[{n:02d}] TEACH {step['concept_name']} {why}")
        print(f"      \"{step['explanation'].get('headline','')}\"  <{step['explanation'].get('source')}>")
        resp = reg.step(sid)

    elif t == "verify":
        resp = reg.teachback(sid, teachback_for(step["concept"]))
        g = resp["grade"]
        print(f"[{n:02d}] VERIFY {step['concept_name']}: scored {g['score']:.2f} ({g['source']}) "
              f"{g['mastery_before']:.2f}->{g['mastery_after']:.2f}")
        if g["missing"]:
            print(f"      missing: {g['missing'][0][:70]}")

    else:
        print(f"[{n:02d}] REPORT: {step.get('reason')}")
        break

st = reg.public_state(sid)
m = st["metrics"]
print("\n--- final ---")
print(f"readiness {m['readiness']:.2f} | accuracy {m['accuracy']:.2f} | "
      f"mastered {len(m['mastered'])}/{len(m['concepts'])} | gaps {m['gaps']}")
if st["diagnosis"]:
    d = st["diagnosis"]
    print(f"diagnosis: {d['name']} | root cause: {d['root_cause_concept']} | conf {d['confidence']}")
for r in m["concepts"]:
    print(f"  {r['mastery']:.2f}  {r['status']:11s} {r['name']}")
print(f"\nllm: {st['engine']['stats']} provider={st['engine']['provider']}")
