"""Turn a pasted syllabus into a topic the engine can teach.

This is the feature that makes the product a platform rather than a demo: the
engine is topic-agnostic, so a generated topic plugs straight in with no engine
change.

The LLM writes a draft; this module is the gatekeeper. Generated content is
*validated hard* because a malformed topic fails silently at runtime, in front of
an audience, instead of at generation time. On validation failure we retry once
with the specific errors fed back to the model. If it still fails we return the
problems rather than persisting something broken.

Honest quality note: generated items are draft quality. The distractor ->
misconception tagging is what makes diagnosis work, and an LLM invents those
tags plausibly but not reliably. Authored topics remain the reference standard.
"""
from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTENT = os.path.join(ROOT, "content")

MIN_CONCEPTS, MAX_CONCEPTS = 3, 8
MAX_ITEMS_PER_CONCEPT = 3


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower())
    return re.sub(r"_+", "_", s).strip("_")[:40] or "concept"


def _parse_syllabus_lines(text: str) -> list[str]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*•0123456789.) \t")
        line = re.sub(r"\s+", " ", line)
        if 3 <= len(line) <= 120:
            out.append(line)
    return out[:40]


def validate(topic: dict) -> tuple[bool, list[str]]:
    """Everything that must be true before a generated topic is allowed to load."""
    errs: list[str] = []
    if not isinstance(topic, dict):
        return False, ["output was not a JSON object"]

    concepts = topic.get("concepts")
    if not isinstance(concepts, list) or not concepts:
        return False, ["missing 'concepts' array"]
    if not (MIN_CONCEPTS <= len(concepts) <= MAX_CONCEPTS):
        errs.append(f"needs {MIN_CONCEPTS}-{MAX_CONCEPTS} concepts, got {len(concepts)}")

    ids, seen = [], set()
    for c in concepts:
        cid = c.get("id")
        if not cid or not isinstance(cid, str):
            errs.append("a concept is missing its id")
            continue
        if cid in seen:
            errs.append(f"duplicate concept id '{cid}'")
        seen.add(cid)
        ids.append(cid)
        if not c.get("name"):
            errs.append(f"concept '{cid}' has no name")
        if not c.get("summary"):
            errs.append(f"concept '{cid}' has no summary")
        ki = c.get("key_ideas")
        if not isinstance(ki, list) or not (2 <= len(ki) <= 5):
            errs.append(f"concept '{cid}' needs 2-5 key_ideas")

    for c in concepts:
        for p in c.get("prereqs") or []:
            if p not in seen:
                errs.append(f"concept '{c.get('id')}' lists unknown prerequisite '{p}'")
            if p == c.get("id"):
                errs.append(f"concept '{c.get('id')}' lists itself as a prerequisite")

    # acyclic?
    graph = {c.get("id"): [p for p in (c.get("prereqs") or []) if p in seen] for c in concepts}
    state: dict[str, int] = {}

    def dfs(n: str) -> bool:
        if state.get(n) == 1:
            return False
        if state.get(n) == 2:
            return True
        state[n] = 1
        for p in graph.get(n, []):
            if not dfs(p):
                return False
        state[n] = 2
        return True

    for n in graph:
        if not dfs(n):
            errs.append("prerequisites form a cycle - the graph must be a DAG")
            break

    mids = set()
    for m in topic.get("misconceptions") or []:
        mid = m.get("id")
        if not mid:
            errs.append("a misconception is missing its id")
            continue
        mids.add(mid)
        for f in ("name", "statement", "why", "repair"):
            if not m.get(f):
                errs.append(f"misconception '{mid}' missing '{f}'")

    items = topic.get("items")
    if not isinstance(items, list) or not items:
        return False, errs + ["missing 'items' array"]

    per_concept: dict[str, int] = {}
    for it in items:
        iid = it.get("id", "?")
        if it.get("concept") not in seen:
            errs.append(f"item '{iid}' references unknown concept '{it.get('concept')}'")
            continue
        per_concept[it["concept"]] = per_concept.get(it["concept"], 0) + 1
        opts = it.get("options")
        if not isinstance(opts, list) or len(opts) != 4:
            errs.append(f"item '{iid}' needs exactly 4 options")
            continue
        n_correct = sum(1 for o in opts if o.get("correct") is True)
        if n_correct != 1:
            errs.append(f"item '{iid}' has {n_correct} correct options, needs exactly 1")
        for o in opts:
            if not o.get("text"):
                errs.append(f"item '{iid}' has an empty option")
            mid = o.get("misconception")
            if mid and mid not in mids:
                errs.append(f"item '{iid}' references unknown misconception '{mid}'")
        if not it.get("stem"):
            errs.append(f"item '{iid}' has no stem")
        try:
            float(it.get("difficulty", 0))
        except (TypeError, ValueError):
            errs.append(f"item '{iid}' has a non-numeric difficulty")

    for cid in ids:
        if per_concept.get(cid, 0) == 0:
            errs.append(f"concept '{cid}' has no items")

    return (len(errs) == 0), errs


def _normalise(topic: dict, subject: str, title: str) -> dict:
    """Coerce into the exact shape engine.core.Content expects."""
    for c in topic["concepts"]:
        c["id"] = slug(c["id"])
        c["prereqs"] = [slug(p) for p in (c.get("prereqs") or [])]
        c.setdefault("analogy", c.get("summary", ""))
        c.setdefault("worked", [])
        c["key_ideas"] = [str(k) for k in c.get("key_ideas", [])][:5]
    for m in topic.get("misconceptions") or []:
        m["id"] = slug(m["id"])
    # remap prereq/item refs after slugging (slug is idempotent for clean ids)
    known = {c["id"] for c in topic["concepts"]}
    for c in topic["concepts"]:
        c["prereqs"] = [p for p in c["prereqs"] if p in known and p != c["id"]]
    for it in topic["items"]:
        it["concept"] = slug(it["concept"])
        try:
            it["difficulty"] = max(-2.5, min(2.5, float(it.get("difficulty", 0))))
        except (TypeError, ValueError):
            it["difficulty"] = 0.0
        it.setdefault("explain", "")
    topic["subject"] = subject or topic.get("subject") or "General"
    topic["title"] = title or topic.get("title") or "Untitled topic"
    topic["blurb"] = topic.get("blurb") or f"Generated from your syllabus: {topic['title']}."
    topic["id"] = slug(topic["title"])
    topic["generated"] = True
    return topic


SYSTEM = (
    "You design assessment content for an adaptive tutor. You output strict JSON only. "
    "Every wrong option you write must be a plausible mistake a real student would make, "
    "and you must tag it with the specific misconception it reveals. Content must be "
    "technically correct and at university level."
)


def _prompt(subject: str, title: str, text: str) -> str:
    lines = _parse_syllabus_lines(text)
    listed = "\n".join(f"- {l}" for l in lines) if lines else "(none supplied)"
    return f"""Build a teachable knowledge graph for this topic.

Subject: {subject or "General"}
Topic: {title or "the syllabus below"}
Syllabus the student pasted:
{listed}

Rules:
- {MIN_CONCEPTS}-{MAX_CONCEPTS} concepts, ordered from foundation to advanced.
- "prereqs" lists concept ids this one depends on. Use [] for foundations. It MUST be acyclic.
- Every concept needs 2-5 "key_ideas" - short, checkable statements a student must hold.
- 2-4 "misconceptions": a named wrong belief a student actually has. Give the wrong
  statement in the student's own voice, why it is wrong, and the repair.
- {MAX_ITEMS_PER_CONCEPT} items per concept. Exactly 4 options, exactly ONE with "correct": true.
  Tag each wrong option with the "misconception" id it evidences, or null if generic.
- "difficulty" is a float from -2 (easy) to +2 (hard).
- ids: lowercase snake_case, no spaces.

Return JSON:
{{"title": "...", "subject": "...", "blurb": "one sentence",
  "concepts": [{{"id": "call_stack", "name": "The Call Stack", "prereqs": [],
                "summary": "one or two sentences", "analogy": "one concrete analogy",
                "key_ideas": ["...", "..."], "worked": ["step 1", "step 2"]}}],
  "misconceptions": [{{"id": "m_no_base", "name": "short name",
                      "statement": "the wrong belief, in the student's voice",
                      "why": "why it is wrong", "repair": "what to do instead"}}],
  "items": [{{"id": "q1", "concept": "call_stack", "difficulty": -0.5,
             "stem": "question text",
             "options": [{{"text": "...", "correct": true, "misconception": null}},
                         {{"text": "...", "correct": false, "misconception": "m_no_base"}}],
             "explain": "why the right answer is right"}}]}}"""


def generate(llm, subject: str, title: str, text: str) -> tuple[dict | None, list[str], str]:
    """Returns (topic, problems, source). topic is None if generation failed."""
    def fallback() -> dict:
        return {"_fail": "the model call did not run"}

    out = llm.json_call(SYSTEM, _prompt(subject, title, text), fallback,
                        tag=f"syllabus:{slug(title or text[:60])}")

    if out.get("_fail"):
        # say WHY: a bad key and an unconfigured key are different problems
        if not llm.available:
            why = "no API key configured - set GROQ_API_KEY or add one in Settings"
        else:
            why = f"{llm.provider} call failed: {llm.last_error or 'unknown error'}"
        return None, [why], out.get("source", "template")

    topic = {k: v for k, v in out.items() if not k.startswith("_") and k != "source"}
    ok, errs = validate(topic)
    if ok:
        return _normalise(topic, subject, title), [], out.get("source", "llm")

    # one repair attempt, with the validator's own complaints fed back
    repair = (f"{_prompt(subject, title, text)}\n\nYour previous attempt was rejected for:\n"
              + "\n".join(f"- {e}" for e in errs[:12])
              + "\nFix exactly these problems and return the full JSON again.")
    out2 = llm.json_call(SYSTEM, repair, lambda: {"_fail": "repair failed"},
                         tag=f"syllabus_repair:{slug(title or text[:60])}")
    if out2.get("_fail"):
        return None, errs, out.get("source", "llm")
    topic2 = {k: v for k, v in out2.items() if not k.startswith("_") and k != "source"}
    ok2, errs2 = validate(topic2)
    if ok2:
        return _normalise(topic2, subject, title), [], out2.get("source", "llm")
    return None, errs2, out.get("source", "llm")


def persist(topic: dict, owner: str | None, store) -> str:
    """Write the generated topic next to the authored ones so it survives restart."""
    os.makedirs(CONTENT, exist_ok=True)
    path = os.path.join(CONTENT, topic["id"] + ".json")
    with open(path, "w") as f:
        json.dump(topic, f, indent=1)
    store.register_topic(topic["id"], owner, topic["subject"], topic["title"])
    return path
