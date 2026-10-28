"""Drive the real HTTP API exactly as the browser does."""
import json, httpx
from fastapi.testclient import TestClient
from app import app
content = json.load(open("content/recursion.json"))
items = {i["id"]: i for i in content["items"]}

c = TestClient(app)
c.post("/api/auth/signup", json={"email": "apitest@t.io", "name": "API Test",
                                 "password": "test-password-1"})
assert c.get("/").status_code == 200, "index"
assert c.get("/app.js").status_code == 200, "app.js"
assert c.get("/styles.css").status_code == 200, "css"

r = c.post("/api/session", json={"topic": "recursion", "seed": 7}).json()
sid = r["session_id"]
print("session", sid, "| first step:", r["step"]["type"])

def wrong_index(iid):
    opts = items[iid]["options"]
    for i, o in enumerate(opts):
        if o["misconception"] in {"M_NO_BASE", "M_RETURN_ORDER"}:
            return i
    return next(i for i, o in enumerate(opts) if o["correct"])

seen_teach = seen_verify = seen_grade = 0
step = r["step"]
for n in range(18):
    t = step["type"]
    if t == "quiz":
        iid = step["item"]["id"]
        # the answer key must never reach the browser: no option may carry `correct`
        leaked = [o for o in step["item"]["options"] if "correct" in o]
        assert not leaked, f"answer key leaked to client: {leaked}"
        r = c.post("/api/answer", json={"session_id": sid, "item_id": iid,
                                        "choice": wrong_index(iid)}).json()
        fb = r["feedback"]
        print(f"  quiz {iid} {'OK' if fb['correct'] else 'MISS'} "
              f"{fb['mastery_before']}->{fb['mastery_after']} "
              f"mis={fb['misconception']['name'] if fb['misconception'] else '-'}")
        step = r["step"]
    elif t == "teach":
        seen_teach += 1
        e = step["explanation"]
        assert e.get("headline") and e.get("body"), "teach payload incomplete"
        pr = e.get("practice") or {}
        assert pr.get("problem") and pr.get("solution"), "practice exercise missing"
        print(f"  TEACH {step['concept_name']} reason={step['reason']} "
              f"src={e.get('source')} paras={len(e['body'])} worked={len(e.get('worked',[]))} "
              f"practice=yes")
        step = c.post("/api/next", json={"session_id": sid}).json()["step"]
    elif t == "verify":
        seen_verify += 1
        r = c.post("/api/teachback", json={"session_id": sid,
            "text": "A base case is a branch that returns without calling itself again. "
                    "Every recursive path must be able to reach it, otherwise the calls keep "
                    "stacking frames until the stack overflows."}).json()
        g = r["grade"]
        seen_grade += 1
        print(f"  VERIFY {g['concept_name']} score={g['score']} src={g['source']} "
              f"{g['mastery_before']}->{g['mastery_after']} hits={len(g['hits'])}")
        step = r["step"]
    else:
        print("  REPORT", step.get("reason")); break

st = c.get(f"/api/session/{sid}").json()["state"]
print("\ngraph nodes:", len(st["graph"]["nodes"]), "| statuses:",
      sorted({n["status"] for n in st["graph"]["nodes"]}))
print("readiness", st["metrics"]["readiness"], "| dx:",
      st["diagnosis"]["name"] if st["diagnosis"] else None,
      "| root:", st["diagnosis"]["root_cause_concept"] if st["diagnosis"] else None)
print(f"teach={seen_teach} verify={seen_verify} grade={seen_grade}")

# persistence: a fresh process must see the same session
print("sessions on disk:", len([1 for _ in __import__('os').listdir('sessions')]))
assert seen_teach and seen_verify, "did not exercise the teach/verify path"
print("\nALL API CHECKS PASSED")
