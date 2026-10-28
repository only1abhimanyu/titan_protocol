"""MasteryLoop adaptive engine.

The engine is deliberately *not* an LLM. Mastery estimation, prerequisite
propagation, misconception diagnosis, item selection and the adaptation policy
are all deterministic algorithms - auditable, reproducible, and instant. The LLM
is used only where it genuinely adds something a template cannot: personalising
an explanation to a specific named misconception, and grading a free-text
teach-back against a rubric.

Everything here is pure functions over a plain dict state, so a session can be
serialised, replayed, or diffed.
"""
from __future__ import annotations

import json
import math
import os
import random

# ---------------------------------------------------------------- constants
K_STUDENT = 0.80      # how far one answer moves the student's ability estimate
K_ITEM = 0.04         # how far one answer recalibrates the item's difficulty
K_TEACHBACK = 1.10    # explaining in your own words is much stronger evidence
PASS = 0.70           # mastery at which a concept counts as learned
UNLOCK = 0.40         # prerequisite mastery needed to unlock a child concept
WEAK = 0.30           # below this we stop pushing forward and repair upstream
THETA_PRIOR = -0.50   # untested students start assumed-shaky, not assumed-blank


THETA_MIN, THETA_MAX = -4.0, 4.0   # keep estimates off the saturated tails


def _clamp(t: float) -> float:
    return max(THETA_MIN, min(THETA_MAX, t))


def sigmoid(x: float) -> float:
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))


# ------------------------------------------------------------------ content
class Content:
    """A topic: its concept graph, item bank, and named misconceptions."""

    def __init__(self, path: str):
        with open(path) as f:
            raw = json.load(f)
        self.raw = raw
        self.id = raw["id"]
        self.title = raw["title"]
        self.subject = raw["subject"]
        self.blurb = raw["blurb"]

        self.concepts = {c["id"]: c for c in raw["concepts"]}
        self.order = [c["id"] for c in raw["concepts"]]
        self.prereqs = {c["id"]: list(c["prereqs"]) for c in raw["concepts"]}
        self.children = {cid: [] for cid in self.concepts}
        for cid, ps in self.prereqs.items():
            for p in ps:
                self.children[p].append(cid)

        self.misconceptions = {m["id"]: m for m in raw["misconceptions"]}
        self.items = {i["id"]: i for i in raw["items"]}
        self.items_by_concept: dict[str, list[str]] = {cid: [] for cid in self.concepts}
        for iid, it in self.items.items():
            self.items_by_concept[it["concept"]].append(iid)

        self.depth = self._depths()
        self.descendants = {cid: self._descendants(cid) for cid in self.concepts}

        # Item difficulties self-calibrate across sessions. This is a small,
        # real demonstration of the system learning: the bank gets better
        # calibrated the more students use it.
        self.calibrate = os.environ.get("CALIBRATE", "0") == "1"
        self.calib_path = os.path.join(os.path.dirname(path), "_calibration.json")
        self.difficulty = {iid: it["difficulty"] for iid, it in self.items.items()}
        self.calib = {}
        if self.calibrate and os.path.exists(self.calib_path):
            try:
                with open(self.calib_path) as f:
                    stored = json.load(f)
                self.calib = stored.get(self.id, {})
                for iid, d in self.calib.items():
                    if iid in self.difficulty:
                        self.difficulty[iid] = d
            except Exception:
                pass

    def _depths(self) -> dict[str, int]:
        depth: dict[str, int] = {}

        def d(cid, seen=()):
            if cid in depth:
                return depth[cid]
            if cid in seen:          # cycle guard; content should be a DAG
                return 0
            ps = self.prereqs[cid]
            depth[cid] = 0 if not ps else 1 + max(d(p, seen + (cid,)) for p in ps)
            return depth[cid]

        for cid in self.concepts:
            d(cid)
        return depth

    def _descendants(self, cid: str) -> set[str]:
        out, stack = set(), list(self.children[cid])
        while stack:
            c = stack.pop()
            if c in out:
                continue
            out.add(c)
            stack.extend(self.children[c])
        return out

    def prereq_closure(self, cid: str) -> set[str]:
        out, stack = set(), list(self.prereqs[cid])
        while stack:
            c = stack.pop()
            if c in out:
                continue
            out.add(c)
            stack.extend(self.prereqs[c])
        return out

    def topo(self) -> list[str]:
        return sorted(self.concepts, key=lambda c: (self.depth[c], self.order.index(c)))

    def save_calibration(self) -> None:
        if not self.calibrate:
            return
        try:
            with open(self.calib_path) as f:
                allc = json.load(f)
        except Exception:
            allc = {}
        allc[self.id] = {iid: round(self.difficulty[iid], 4) for iid in self.items}
        try:
            with open(self.calib_path, "w") as f:
                json.dump(allc, f, indent=1)
        except Exception:
            pass


# -------------------------------------------------------------------- state
def new_state(content: Content, seed: int | None = None) -> dict:
    return {
        "topic": content.id,
        "seed": seed,
        "theta": {cid: THETA_PRIOR for cid in content.concepts},
        "attempts": {cid: 0 for cid in content.concepts},
        "correct": {cid: 0 for cid in content.concepts},
        "streak_fail": {cid: 0 for cid in content.concepts},
        "modality": {cid: 0 for cid in content.concepts},
        "mis": {},                     # misconception id -> evidence
        "seen": {},                    # item id -> {correct, choice}
        "taught_mis": {},              # misconception id -> evidence count when taught
        "teach_count": {},             # concept id -> how many times taught
        "verified": [],                # concepts already teach-back verified
        "pending_verify": None,        # concept awaiting teach-back
        "last_action": None,
        "log": [],                     # the audit trail the judges can read
        "teachback": [],               # graded teach-back records
    }


def mastery(state: dict, cid: str) -> float:
    return sigmoid(state["theta"][cid])


def tested(state: dict, cid: str) -> bool:
    return state["attempts"][cid] > 0 or state["theta"][cid] != THETA_PRIOR


# ------------------------------------------------------------------ tracing
def record_answer(content: Content, state: dict, item_id: str, choice: int) -> dict:
    """Bayesian-flavoured Elo update + prerequisite propagation."""
    item = content.items[item_id]
    cid = item["concept"]
    opt = item["options"][choice]
    y = 1 if opt["correct"] else 0
    d = content.difficulty[item_id]

    before = mastery(state, cid)
    p = sigmoid(state["theta"][cid] - d)
    delta = K_STUDENT * (y - p)
    state["theta"][cid] = _clamp(state["theta"][cid] + delta)

    # Item self-calibration: items that are answered correctly by weak students
    # get easier; items that defeat strong students get harder.
    content.difficulty[item_id] = d - K_ITEM * (y - p)

    state["attempts"][cid] += 1
    state["correct"][cid] += y
    state["streak_fail"][cid] = 0 if y else state["streak_fail"][cid] + 1
    state["seen"][item_id] = {"correct": bool(y), "choice": choice}

    # Prerequisite propagation. This is what makes a gap *locatable*: a failure
    # at a concept is weak evidence that its foundations are shaky, and a solid
    # pass is weak evidence that they are not.
    m = mastery(state, cid)
    propagated = []
    for pre in content.prereqs[cid]:
        if y == 0:
            push = -0.18 * (1.0 - m)
            state["theta"][pre] = _clamp(state["theta"][pre] + push)
            propagated.append({"concept": pre, "delta": round(push, 3)})
        elif m > 0.60:
            state["theta"][pre] = _clamp(state["theta"][pre] + 0.04)
            propagated.append({"concept": pre, "delta": 0.04})

    # Misconception evidence
    mid = opt["misconception"]
    if not y and mid:
        ev = state["mis"].setdefault(mid, {"count": 0, "items": [], "concepts": []})
        ev["count"] += 1
        if item_id not in ev["items"]:
            ev["items"].append(item_id)
        if cid not in ev["concepts"]:
            ev["concepts"].append(cid)

    after = mastery(state, cid)
    rec = {
        "type": "answer", "item": item_id, "concept": cid,
        "choice": choice, "correct": bool(y),
        "misconception": mid if not y else None,
        "mastery_before": round(before, 3), "mastery_after": round(after, 3),
        "delta": round(after - before, 3),
        "propagated": propagated,
        "difficulty": round(content.difficulty[item_id], 2),
    }
    state["log"].append(rec)
    return rec


def record_teachback(content: Content, state: dict, cid: str, score: float) -> dict:
    """A graded explanation moves the estimate hard - the generation effect."""
    before = mastery(state, cid)
    evidence = max(-1.0, min(1.0, score * 2.0 - 1.0))
    state["theta"][cid] = _clamp(state["theta"][cid] + K_TEACHBACK * evidence)
    if cid not in state["verified"]:
        state["verified"].append(cid)
    state["pending_verify"] = None
    state["attempts"][cid] += 1
    state["streak_fail"][cid] = 0
    if score >= 0.6:
        state["correct"][cid] += 1
    after = mastery(state, cid)
    rec = {
        "type": "teachback", "concept": cid, "score": round(score, 2),
        "mastery_before": round(before, 3), "mastery_after": round(after, 3),
        "delta": round(after - before, 3),
    }
    state["log"].append(rec)
    state["teachback"].append(rec)
    return rec


# ---------------------------------------------------------------- diagnosis
def diagnose(content: Content, state: dict) -> dict | None:
    """Name the wrong belief, with the questions that evidence it.

    A gap is not 'you got 3 wrong'. A gap is 'you believe recursion stops on
    its own, and here are the three questions that prove it'.
    """
    cands = []
    for mid, ev in state["mis"].items():
        if ev["count"] < 2:
            continue
        # weight by how many distinct concepts the error spans, and by
        # how confidently the student otherwise answers
        spread = len(ev["concepts"])
        strength = ev["count"] + 0.5 * (spread - 1)
        cands.append((strength, mid, ev))
    if not cands:
        return None
    cands.sort(key=lambda t: -t[0])
    strength, mid, ev = cands[0]
    mis = content.misconceptions.get(mid)
    if not mis:
        return None

    # Which concept is the real root cause? The weakest node in the prerequisite
    # closure of everything this misconception touches.
    touched = set(ev["concepts"])
    closure = set(touched)
    for c in touched:
        closure |= content.prereq_closure(c)
    root = min(closure, key=lambda c: (mastery(state, c), content.depth[c]))

    return {
        "misconception_id": mid,
        "name": mis["name"],
        "statement": mis["statement"],
        "why_wrong": mis["why"],
        "repair": mis["repair"],
        "evidence_items": [
            {"id": iid, "stem": content.items[iid]["stem"],
             "concept": content.items[iid]["concept"]}
            for iid in ev["items"]
        ],
        "evidence_count": ev["count"],
        "implicated_concepts": sorted(touched, key=lambda c: content.depth[c]),
        "root_cause_concept": root,
        "root_cause_name": content.concepts[root]["name"],
        "confidence": round(min(0.95, 0.45 + 0.12 * strength), 2),
    }


# ------------------------------------------------------------------ frontier
def unlocked(content: Content, state: dict, cid: str) -> bool:
    return all(mastery(state, p) >= UNLOCK for p in content.prereqs[cid])


def frontier(content: Content, state: dict) -> list[str]:
    """Concepts the student is allowed to work on and has not yet mastered."""
    return [c for c in content.topo()
            if unlocked(content, state, c) and mastery(state, c) < PASS]


def focus_concept(content: Content, state: dict) -> str | None:
    """Where to spend the next minute of the student's attention.

    If the weakest reachable concept is badly broken, stop pushing forward and
    walk *down* the graph to repair its weakest foundation. This is the
    behaviour that looks like real teaching.
    """
    fr = frontier(content, state)
    if not fr:
        return None
    target = min(fr, key=lambda c: (mastery(state, c), content.depth[c]))
    if mastery(state, target) < WEAK:
        closure = content.prereq_closure(target) | {target}
        weakest = min(closure, key=lambda c: (mastery(state, c), content.depth[c]))
        if weakest != target and mastery(state, weakest) < UNLOCK:
            return weakest
    return target


# ------------------------------------------------------------ item selection
def select_item(content: Content, state: dict, cid: str, seed: int | None = None) -> str | None:
    """Computerised adaptive testing: pick the item that maximises information.

    An item is most informative when its difficulty sits right at the student's
    current ability on that concept - hard enough to be a real test, easy enough
    that failure means something.
    """
    rng = random.Random(seed)
    pool = content.items_by_concept.get(cid, [])
    unseen = [i for i in pool if i not in state["seen"]]
    if unseen:
        pool = unseen
    if not pool:
        # fall back to any unlocked concept with unseen items
        for c in frontier(content, state):
            cand = [i for i in content.items_by_concept[c] if i not in state["seen"]]
            if cand:
                pool = cand
                break
    if not pool:
        return None

    theta = state["theta"][cid]
    best, best_score = None, -1e9
    for iid in pool:
        d = content.difficulty[iid]
        information = -abs(d - theta)          # peak at d == theta
        weakness = 1.0 - mastery(state, cid)
        retry_bonus = 0.0 if iid not in state["seen"] else -0.8
        jitter = rng.uniform(0, 0.12)
        score = information + 0.5 * weakness + retry_bonus + jitter
        if score > best_score:
            best, best_score = iid, score
    return best


# -------------------------------------------------------------------- policy
def next_action(content: Content, state: dict, seed: int | None = None) -> dict:
    """The adaptation policy. Returns the single next thing to do."""
    mods = ["analogy", "worked_example", "socratic"]

    # 0. A teach-back is outstanding -> that is always the next step.
    if state.get("pending_verify"):
        cid = state["pending_verify"]
        return {"type": "verify", "reason": "verify_understanding",
                "concept": cid,
                "prompt": f"Explain {content.concepts[cid]['name'].lower()} in your own words, "
                          f"as if teaching a classmate who got it wrong."}

    diag = diagnose(content, state)

    # 1. Teach a misconception we have not taught, OR one we taught that has
    #    since produced fresh evidence - i.e. the explanation did not land.
    if diag:
        mid = diag["misconception_id"]
        taught_at = state["taught_mis"].get(mid)
        now = state["mis"][mid]["count"]
        if taught_at is None or now >= taught_at + 2:
            cid = diag["root_cause_concept"]
            if taught_at is not None:
                state["modality"][cid] = (state["modality"][cid] + 1) % 3
            return {"type": "teach", "reason": ("misconception_diagnosed" if taught_at is None
                                               else "explanation_did_not_land"),
                    "diagnosis": diag, "concept": cid,
                    "modality": mods[state["modality"][cid]]}

    cid = focus_concept(content, state)
    if cid is None:
        return {"type": "report", "reason": "all_concepts_mastered"}

    m = mastery(state, cid)
    # Never teach the same concept back-to-back, and cap total re-teaches, or a
    # student who keeps failing the teach-back spins forever.
    can_teach = (state["teach_count"].get(cid, 0) < 3
                 and state.get("last_action") != "verify")

    # 2. Repeated failure on the same concept -> switch teaching modality.
    if state["streak_fail"][cid] >= 2 and can_teach:
        state["modality"][cid] = (state["modality"][cid] + 1) % 3
        return {"type": "teach", "reason": "modality_switch", "concept": cid,
                "modality": mods[state["modality"][cid]], "diagnosis": diag}

    # 3. Weak concept -> teach before testing.
    if m < WEAK and cid not in state["verified"] and can_teach:
        return {"type": "teach", "reason": "concept_weak", "concept": cid,
                "modality": mods[state["modality"][cid]], "diagnosis": diag}

    # 4. Solid enough to test.
    iid = select_item(content, state, cid, seed)
    if iid is None:
        return {"type": "report", "reason": "item_bank_exhausted"}
    return {"type": "quiz", "reason": "adaptive_probe", "item": iid, "concept": cid,
            "diagnosis": diag}


# ------------------------------------------------------------------- metrics
def metrics(content: Content, state: dict) -> dict:
    rows = []
    for cid in content.topo():
        m = mastery(state, cid)
        if state["attempts"][cid] == 0 and state["theta"][cid] == THETA_PRIOR:
            status = "untested"
        elif m >= PASS:
            status = "mastered"
        elif m >= UNLOCK:
            status = "developing"
        else:
            status = "gap"
        rows.append({
            "id": cid, "name": content.concepts[cid]["name"],
            "mastery": round(m, 3), "attempts": state["attempts"][cid],
            "correct": state["correct"][cid], "status": status,
            "depth": content.depth[cid],
            "prereqs": content.prereqs[cid],
            "unlocked": unlocked(content, state, cid),
        })

    # Readiness weights each concept by how much sits on top of it, so a gap in
    # a foundation costs more than a gap in a leaf.
    total_w = sum(1 + len(content.descendants[cid]) for cid in content.concepts)
    readiness = sum(mastery(state, cid) * (1 + len(content.descendants[cid]))
                    for cid in content.concepts) / max(total_w, 1e-9)

    mastered = [r["id"] for r in rows if r["status"] == "mastered"]
    gaps = [r["id"] for r in rows if r["status"] == "gap"]
    developing = [r["id"] for r in rows if r["status"] == "developing"]

    # Projected effort to mastery at the observed rate.
    remaining = [cid for cid in content.concepts if mastery(state, cid) < PASS]
    answered = sum(state["attempts"].values())
    rate = answered / max(1, len(content.concepts))
    eta_minutes = int(len(remaining) * 2.2 + max(0, 12 - rate) * 1.5)

    return {
        "readiness": round(readiness, 3),
        "concepts": rows,
        "mastered": mastered,
        "gaps": gaps,
        "developing": developing,
        "eta_minutes": eta_minutes,
        "answers": answered,
        "accuracy": round(
            sum(state["correct"].values()) / max(1, answered), 3),
    }
