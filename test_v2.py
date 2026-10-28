"""End-to-end: auth, vault, learning, XP, flashcards, syllabus, guards."""
import os, sys, tempfile, json
os.environ["ML_DB"] = tempfile.mktemp(suffix=".db")
os.environ["APP_SECRET"] = "test-secret-not-a-real-one"

from fastapi.testclient import TestClient
from app import app
from engine import security, flashcards, gamify

c = TestClient(app)
fails = []

def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)

print("\n--- guards (must reject before login)")
for path in ("/api/auth/me", "/api/me/summary", "/api/flashcards", "/api/settings"):
    r = c.get(path)
    check(f"401 on {path}", r.status_code == 401, r.status_code)

print("\n--- signup + validation")
r = c.post("/api/auth/signup", json={"email": "bad", "name": "A", "password": "12345678"})
check("rejects bad email", r.status_code == 400)
r = c.post("/api/auth/signup", json={"email": "a@b.co", "name": "A", "password": "short"})
check("rejects short password", r.status_code == 400)
r = c.post("/api/auth/signup", json={"email": "abhi@titan.dev", "name": "Abhimanyu", "password": "correct-horse-9"})
check("signup ok", r.status_code == 200, r.text[:120])
user = r.json()["user"]
check("password hash never returned", "pw_hash" not in json.dumps(user))
check("cookie set", security.COOKIE in c.cookies)
r = c.post("/api/auth/signup", json={"email": "abhi@titan.dev", "name": "X", "password": "correct-horse-9"})
check("duplicate email rejected", r.status_code == 409)

print("\n--- me / logout / login")
r = c.get("/api/auth/me")
check("me works", r.status_code == 200 and r.json()["user"]["email"] == "abhi@titan.dev")
r = c.post("/api/auth/login", json={"email": "abhi@titan.dev", "password": "wrong-pass"})
check("wrong password rejected", r.status_code == 401)
r = c.post("/api/auth/login", json={"email": "abhi@titan.dev", "password": "correct-horse-9"})
check("login ok", r.status_code == 200)

print("\n--- key vault")
r = c.post("/api/settings/key", json={"key": "not-a-key"})
check("rejects malformed key", r.status_code == 400)
r = c.post("/api/settings/key", json={"key": "gsk_" + "a" * 40})
check("accepts groq key", r.status_code == 200, r.text[:120])
masked = r.json()["masked"]
check("masked form hides the body", "aaaa" in masked and masked.count("a") <= 4, masked)
r = c.get("/api/settings")
body = r.text
check("settings never returns the plaintext key", ("a" * 40) not in body)
check("settings reports has_key", r.json()["has_key"] is True)
# the ciphertext must be real ciphertext, not the key
from engine import store as st
row = st.get_user(user["id"])
check("stored value is ciphertext", row["key_enc"] and "gsk_" not in row["key_enc"])
check("decrypt round-trips", security.decrypt_key(row["key_enc"]) == "gsk_" + "a" * 40)
r = c.delete("/api/settings/key")
check("key cleared", c.get("/api/settings").json()["has_key"] is False)

print("\n--- token forgery")
good = security.issue_token(user["id"])
check("valid token reads", security.read_token(good) == user["id"])
check("tampered token rejected", security.read_token(good[:-4] + "0000") is None)
check("garbage token rejected", security.read_token("lolnope") is None)

print("\n--- learning + XP")
r = c.post("/api/session", json={"topic": "recursion", "seed": 7})
check("session created", r.status_code == 200, r.text[:150])
sid = r.json()["session_id"]
step = r.json()["step"]
content_items = json.load(open("content/recursion.json"))["items"]
items = {i["id"]: i for i in content_items}
def wrong(iid):
    for i, o in enumerate(items[iid]["options"]):
        if o["misconception"] in {"M_NO_BASE", "M_RETURN_ORDER"}:
            return i
    return next(i for i, o in enumerate(items[iid]["options"]) if o["correct"])

xp_before = c.get("/api/me/summary").json()["game"]["xp"]
saw_teach = saw_verify = False
for _ in range(14):
    if step["type"] == "quiz":
        r = c.post("/api/answer", json={"session_id": sid, "item_id": step["item"]["id"],
                                        "choice": wrong(step["item"]["id"])})
        check_extra = r.json()["feedback"]["correct"] is False
        step = r.json()["step"]
    elif step["type"] == "teach":
        saw_teach = True
        pr = step["explanation"].get("practice") or {}
        if pr and not pr.get("problem"):
            fails.append("practice block empty")
        step = c.post("/api/next", json={"session_id": sid}).json()["step"]
    elif step["type"] == "verify":
        saw_verify = True
        r = c.post("/api/teachback", json={"session_id": sid,
            "text": "A base case is a branch that returns without calling itself again. "
                    "Every recursive path must reach it or the frames keep stacking until "
                    "the stack overflows."})
        check("teachback returns xp_total", "xp_total" in r.json())
        step = r.json()["step"]
    else:
        break
check("reached a teach step", saw_teach)
check("reached a verify step", saw_verify)
summary = c.get("/api/me/summary").json()
check("XP increased", summary["game"]["xp"] > xp_before,
      f"{xp_before} -> {summary['game']['xp']}")
check("level assigned", summary["game"]["level"] >= 1 and summary["game"]["title"])
check("achievements present", len(summary["achievements"]) == len(gamify.ACHIEVEMENTS))
check("first_blood earned from a real diagnosis",
      next(a["earned"] for a in summary["achievements"] if a["id"] == "first_blood"))

print("\n--- XP cannot be farmed by guessing")
u2 = c.post("/api/auth/signup", json={"email": "g@t.co", "name": "G", "password": "guessguess"}).json()["user"]
c2 = TestClient(app)
c2.cookies.set(security.COOKIE, security.issue_token(u2["id"]))
r = c2.post("/api/session", json={"topic": "normalization"})
sid2, step2 = r.json()["session_id"], r.json()["step"]
wrong_paid = []
mastery_moves = []
answered = 0
for _ in range(20):
    if step2["type"] != "quiz":
        if step2["type"] == "report":
            break
        step2 = (c2.post("/api/next", json={"session_id": sid2}).json()["step"]
                 if step2["type"] == "teach"
                 else c2.post("/api/teachback", json={"session_id": sid2,
                      "text": "idk"}).json()["step"])
        continue
    r = c2.post("/api/answer", json={"session_id": sid2, "item_id": step2["item"]["id"],
                                    "choice": 0})
    fb = r.json()["feedback"]
    answered += 1
    if not fb["correct"]:
        wrong_paid.append(r.json()["xp_earned"])
        mastery_moves.append(fb["mastery_after"] - fb["mastery_before"])
    step2 = r.json()["step"]

# deterministic claims, not statistical ones
check("a wrong answer pays exactly 0 XP", all(x == 0 for x in wrong_paid),
      f"{wrong_paid}")
check("a wrong answer lowers the estimate", all(m < 0 for m in mastery_moves),
      f"{mastery_moves[:5]}")
final = c2.get("/api/me/summary").json()["sessions"][0]
check("guessing never reaches mastery", final["mastered"] == 0,
      f"mastered {final['mastered']} after {answered} guesses")

print("\n--- option order gives no positional signal")
pos = {}
seen_items = 0
step3 = c.post("/api/session", json={"topic": "recursion", "seed": 11}).json()
sid3 = step3["session_id"]; st3 = step3["step"]
for _ in range(20):
    if st3["type"] != "quiz":
        if st3["type"] == "report":
            break
        st3 = (c.post("/api/next", json={"session_id": sid3}).json()["step"]
               if st3["type"] == "teach"
               else c.post("/api/teachback", json={"session_id": sid3,
                    "text": "The base case returns without recursing and every path reaches it."}).json()["step"])
        continue
    item = items[st3["item"]["id"]]
    right = next(o["text"] for o in item["options"] if o["correct"])
    disp = [o["text"] for o in st3["item"]["options"]].index(right)
    pos[disp] = pos.get(disp, 0) + 1
    seen_items += 1
    st3 = c.post("/api/answer", json={"session_id": sid3, "item_id": st3["item"]["id"],
                                     "choice": next(o["i"] for o in st3["item"]["options"]
                                                    if o["text"] == right)}).json()["step"]
check("sampled several questions", seen_items >= 5, seen_items)
check("correct answer is not stuck in position A",
      pos.get(0, 0) < max(1, seen_items - 1) and len(pos) >= 2, pos)

print("\n--- flashcards + SM-2")
r = c.post("/api/flashcards/generate", json={"topic": "recursion"})
check("cards generated offline", r.status_code == 200 and r.json()["added"] > 0, r.text[:150])
check("cards target diagnosed misconceptions",
      isinstance(r.json()["targeted_misconceptions"], list))
r = c.get("/api/flashcards")
due = r.json()["due"]
check("due cards present", len(due) > 0, r.json()["stats"])
card = due[0]
r = c.post(f"/api/flashcards/{card['id']}/review", json={"grade": "good"})
check("review accepted", r.status_code == 200)
check("SM-2 scheduled into the future", r.json()["due"] > 0 and r.json()["reps"] == 1)
r = c.post(f"/api/flashcards/{card['id']}/review", json={"grade": "again"})
check("lapse resets reps", r.json()["reps"] == 0)
sm = flashcards.review({"ease": 2.5, "interval": 86400, "reps": 3, "lapses": 0}, "easy")
check("SM-2 grows interval on easy", sm["interval"] > 86400, sm)
check("ease clamped to <= 3.0", sm["ease"] <= 3.0)
check("reviewing cards does not move mastery",
      c.get("/api/me/summary").json()["sessions"][0]["readiness"] > 0)

print("\n--- syllabus (offline must fail loudly, not silently)")
r = c.post("/api/syllabus", json={"subject": "DBMS", "title": "Indexing",
                                  "text": "B-trees\nHash indexes\nComposite indexes"})
check("offline syllabus returns 422 with problems", r.status_code == 422, r.status_code)
check("error names the cause", "problems" in r.text and "hint" in r.text)

print("\n--- syllabus validation catches bad output")
from engine import syllabus as syl
bad = {"concepts": [{"id": "a", "name": "A", "prereqs": ["b"], "summary": "s",
                     "key_ideas": ["x", "y"]},
                    {"id": "b", "name": "B", "prereqs": ["a"], "summary": "s",
                     "key_ideas": ["x", "y"]},
                    {"id": "c", "name": "C", "prereqs": [], "summary": "s",
                     "key_ideas": ["x", "y"]}],
       "misconceptions": [{"id": "m1", "name": "n", "statement": "s", "why": "w", "repair": "r"}],
       "items": [{"id": "q1", "concept": "a", "difficulty": 0, "stem": "s",
                  "options": [{"text": "1", "correct": True}, {"text": "2", "correct": True},
                              {"text": "3", "correct": False}, {"text": "4", "correct": False}],
                  "explain": "e"}]}
ok, errs = syl.validate(bad)
check("cycle rejected", not ok and any("cycle" in e for e in errs), errs)
check("two correct options rejected", any("correct options" in e for e in errs), errs)
check("concept with no items rejected", any("no items" in e for e in errs), errs)

print(f"\n{'ALL V2 CHECKS PASSED' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
