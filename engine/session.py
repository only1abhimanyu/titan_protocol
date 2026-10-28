"""Session orchestration: turns an engine action into a renderable step."""
from __future__ import annotations

import json
import os
import random
import threading
import uuid
from datetime import datetime, timezone

from . import core, tutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTENT_DIR = os.path.join(ROOT, "content")
SESS_DIR = os.path.join(ROOT, "sessions")


class Registry:
    def __init__(self, llm):
        self.llm = llm
        self.topics: dict[str, core.Content] = {}
        self.sessions: dict[str, dict] = {}
        self.lock = threading.Lock()
        os.makedirs(SESS_DIR, exist_ok=True)
        for fn in sorted(os.listdir(CONTENT_DIR)):
            if fn.endswith(".json") and not fn.startswith("_"):
                c = core.Content(os.path.join(CONTENT_DIR, fn))
                self.topics[c.id] = c
        self._load()

    # ------------------------------------------------------------- persistence
    def _load(self):
        for fn in os.listdir(SESS_DIR):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(SESS_DIR, fn)) as f:
                        s = json.load(f)
                    if s.get("topic") in self.topics:
                        self.sessions[s["id"]] = s
                except Exception:
                    continue

    def _save(self, sess: dict):
        try:
            with open(os.path.join(SESS_DIR, sess["id"] + ".json"), "w") as f:
                json.dump(sess, f)
        except Exception:
            pass

    # ------------------------------------------------------------------ topics
    def topic_list(self) -> list[dict]:
        return [{
            "id": c.id, "title": c.title, "subject": c.subject, "blurb": c.blurb,
            "concepts": len(c.concepts), "items": len(c.items),
            "misconceptions": len(c.misconceptions),
            "generated": bool(c.raw.get("generated")),
        } for c in self.topics.values()]

    def get(self, sid: str):
        sess = self.sessions.get(sid)
        if not sess:
            return None, None
        return sess, self.topics.get(sess["topic"])

    # ------------------------------------------------------------------ create
    def load_topic(self, path: str):
        """Register a freshly generated topic without restarting the server."""
        c = core.Content(path)
        self.topics[c.id] = c
        return c

    def create(self, topic_id: str, user_id: str | None = None,
               seed: int | None = None, llm=None) -> dict:
        content = self.topics[topic_id]
        sid = uuid.uuid4().hex[:12]
        sess = {
            "id": sid,
            "topic": topic_id,
            "user_id": user_id,
            "created": datetime.now(timezone.utc).isoformat(),
            "state": core.new_state(content, seed),
            "steps": 0,
        }
        with self.lock:
            self.sessions[sid] = sess
        self._save(sess)
        return self.step(sid, llm=llm)

    # -------------------------------------------------------------------- step
    def step(self, sid: str, note: str | None = None, llm=None) -> dict:
        sess, content = self.get(sid)
        if not sess:
            raise KeyError(sid)
        state = sess["state"]

        action = core.next_action(content, state, state.get("seed"))
        sess["steps"] += 1
        state["last_action"] = action.get("type")
        rendered = self._render(content, state, action, llm or self.llm, sid)

        # A teach action commits the session to a teach-back before anything else.
        if action["type"] == "teach":
            cid = action["concept"]
            state["pending_verify"] = cid
            state["teach_count"][cid] = state["teach_count"].get(cid, 0) + 1
            mid = (action.get("diagnosis") or {}).get("misconception_id")
            if mid and mid in state["mis"]:
                state["taught_mis"][mid] = state["mis"][mid]["count"]

        state["log"].append({"type": "step", "step": sess["steps"],
                             "action": action["type"], "reason": action.get("reason")})
        sess["last_step"] = rendered
        self._save(sess)
        return {
            "session_id": sid,
            "step": rendered,
            "state": self.public_state(sid),
            "note": note,
        }

    # ------------------------------------------------------------------ render
    def _render(self, content, state, action, llm=None, sid: str = "") -> dict:
        t = action["type"]
        if t == "quiz":
            item = content.items[action["item"]]
            cid = item["concept"]
            # Display order is shuffled server-side, seeded by (session, item) so a
            # re-render or a resume shows the same order. Each option keeps its
            # ORIGINAL index in `i`, which is what the client sends back - so the
            # answer key never has to leave the server and position gives no signal.
            opts = [{"i": i, "text": o["text"]} for i, o in enumerate(item["options"])]
            random.Random(f"{sid}:{item['id']}").shuffle(opts)
            return {
                "type": "quiz",
                "reason": action.get("reason"),
                "item": {
                    "id": item["id"],
                    "stem": item["stem"],
                    "options": opts,
                },
                "concept": cid,
                "concept_name": content.concepts[cid]["name"],
                "mastery": round(core.mastery(state, cid), 3),
                "difficulty": round(content.difficulty[item["id"]], 2),
                "diagnosis": action.get("diagnosis"),
            }
        if t == "teach":
            cid = action["concept"]
            expl = tutor.explain(llm or self.llm, content, state, cid,
                                 action.get("modality", "analogy"),
                                 action.get("diagnosis"))
            return {
                "type": "teach",
                "reason": action.get("reason"),
                "concept": cid,
                "concept_name": content.concepts[cid]["name"],
                "explanation": expl,
                "mastery": round(core.mastery(state, cid), 3),
                "diagnosis": action.get("diagnosis"),
            }
        if t == "verify":
            cid = action["concept"]
            return {
                "type": "verify",
                "reason": action.get("reason"),
                "concept": cid,
                "concept_name": content.concepts[cid]["name"],
                "prompt": action["prompt"],
                "mastery": round(core.mastery(state, cid), 3),
            }
        return {"type": "report", "reason": action.get("reason")}

    # ------------------------------------------------------------------ inputs
    def answer(self, sid: str, item_id: str, choice: int, llm=None) -> dict:
        sess, content = self.get(sid)
        if not sess:
            raise KeyError(sid)
        if item_id not in content.items:
            raise ValueError("unknown item")
        item = content.items[item_id]
        if not (0 <= choice < len(item["options"])):
            raise ValueError("choice out of range")
        rec = core.record_answer(content, sess["state"], item_id, choice)
        content.save_calibration()
        feedback = {
            "concept": rec["concept"],
            "concept_name": content.concepts[rec["concept"]]["name"],
            "correct": rec["correct"],
            "correct_text": next(o["text"] for o in item["options"] if o["correct"]),
            "explain": item["explain"],
            "misconception": None,
            "mastery_before": rec["mastery_before"],
            "mastery_after": rec["mastery_after"],
            "propagated": rec["propagated"],
        }
        if rec["misconception"]:
            mis = content.misconceptions[rec["misconception"]]
            feedback["misconception"] = {"id": rec["misconception"], "name": mis["name"]}
        out = self.step(sid, llm=llm)
        out["feedback"] = feedback
        return out

    def teachback(self, sid: str, text: str, llm=None) -> dict:
        sess, content = self.get(sid)
        if not sess:
            raise KeyError(sid)
        state = sess["state"]
        cid = state.get("pending_verify") or core.focus_concept(content, state)
        if not cid:
            raise ValueError("nothing to verify")
        g = tutor.grade(llm or self.llm, content, state, cid, text or "")
        rec = core.record_teachback(content, state, cid, g["score"])
        out = self.step(sid, llm=llm)
        out["grade"] = {
            "score": g["score"],
            "hits": g.get("hits", []),
            "missing": g.get("missing", []),
            "feedback": g.get("feedback", ""),
            "source": g.get("source", "template"),
            "concept": cid,
            "concept_name": content.concepts[cid]["name"],
            "mastery_before": rec["mastery_before"],
            "mastery_after": rec["mastery_after"],
            "delta": rec["delta"],
        }
        return out

    def user_sessions(self, uid: str) -> list[dict]:
        """Every session this user owns, newest activity first, with live metrics."""
        out = []
        for sess in self.sessions.values():
            if sess.get("user_id") != uid:
                continue
            content = self.topics.get(sess["topic"])
            if not content:
                continue
            state = sess["state"]
            m = core.metrics(content, state)
            out.append({
                "session_id": sess["id"], "topic": sess["topic"],
                "title": content.title, "subject": content.subject,
                "readiness": m["readiness"], "mastered": len(m["mastered"]),
                "concepts": len(m["concepts"]), "answers": m["answers"],
                "accuracy": m["accuracy"], "steps": sess["steps"],
                "eta_minutes": m["eta_minutes"],
                "state": state, "metrics": m,
                "diagnosis": core.diagnose(content, state),
                "teachback": state.get("teachback") or [],
            })
        out.sort(key=lambda x: -x["steps"])
        return out

    def latest_for(self, uid: str, topic: str) -> dict | None:
        for s in self.user_sessions(uid):
            if s["topic"] == topic:
                return s
        return None

    def resume(self, sid: str) -> dict:
        """Reopen a session exactly where it was left - used by the demo and by ?sid=."""
        sess, _ = self.get(sid)
        if not sess:
            raise KeyError(sid)
        return {"session_id": sid, "step": sess.get("last_step") or {},
                "state": self.public_state(sid)}

    # ------------------------------------------------------------------- state
    def public_state(self, sid: str) -> dict:
        sess, content = self.get(sid)
        if not sess:
            raise KeyError(sid)
        state = sess["state"]
        m = core.metrics(content, state)
        diag = core.diagnose(content, state)
        return {
            "topic": content.id,
            "title": content.title,
            "subject": content.subject,
            "metrics": m,
            "diagnosis": diag,
            "graph": {
                "nodes": [{
                    "id": c["id"], "name": c["name"], "depth": content.depth[c["id"]],
                    "prereqs": content.prereqs[c["id"]],
                    "mastery": round(core.mastery(state, c["id"]), 3),
                    "status": next(r["status"] for r in m["concepts"] if r["id"] == c["id"]),
                    "weight": 1 + len(content.descendants[c["id"]]),
                    "attempts": state["attempts"][c["id"]],
                } for c in content.raw["concepts"]],
            },
            "log": state["log"][-40:],
            "teachback": state["teachback"],
            "engine": {
                "provider": self.llm.provider,
                "stats": dict(self.llm.stats),
                "steps": sess["steps"],
            },
        }
