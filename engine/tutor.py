"""Tutor: personalised explanation + teach-back grading.

Two jobs, each with a real fallback:

  1. explain()  - turn a concept + a *named* misconception + the student's own
                  numbers into an explanation that could only be for this
                  student. The template fallback is hand-composed from authored
                  content, so it is genuinely good, not a stub.

  2. grade()    - score a free-text teach-back against the concept's rubric of
                  key ideas. Fallback is a lexical overlap grader, which is
                  crude but honest, and it is what runs if the network dies.
"""
from __future__ import annotations

import re

from . import core

STOP = set("""a an the and or but if then than that this these those is are was were be been being
of in on at to for from with without by as it its it's you your yours we our i me my he she they them
their there here what which who whom when where why how do does did done can could should would may
might must will shall not no nor so such only own same very just also more most other some any each
into over under again further once about above below between both few many much because while during
after before up down out off then once all am""".split())


def _stem(w: str) -> str:
    w = w.lower()
    for suf in ("ational", "ations", "ation", "ings", "ing", "ies", "ied", "es", "ed", "s", "ly"):
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z'-]+", text or "")
    return {_stem(w) for w in words if len(w) > 3 and w.lower() not in STOP}


# --------------------------------------------------------------------- explain
def explain(llm, content, state, cid, modality="analogy", diagnosis=None) -> dict:
    concept = content.concepts[cid]
    mis = None
    if diagnosis and diagnosis.get("root_cause_concept") == cid:
        mid = diagnosis["misconception_id"]
        mis = content.misconceptions.get(mid)
        if mis:
            mis = dict(mis, id=mid)

    def fb_practice() -> dict:
        """A predict-then-verify exercise built from the concept's own mechanics.

        Deliberately not graded and not fed into mastery: assessment is the
        adaptive part, practice is rehearsal. Keeps the mastery model honest.
        """
        ideas, worked = concept["key_ideas"], concept.get("worked", [])
        return {
            "problem": ("Take the worked example above and change one input - a different "
                        "number, a different depth. Write down what you expect to happen, and "
                        "why, before you check it against the steps."),
            "hint": (f"Anchor on the mechanism: {ideas[-1]}." if ideas
                     else concept["analogy"]),
            "solution": ("  ->  ".join(worked) if worked else concept["summary"]),
        }

    def fallback() -> dict:
        m = core.mastery(state, cid)
        att, cor = state["attempts"][cid], state["correct"][cid]
        body = [concept["summary"]]

        if mis:
            body.append(
                f"**The belief that is costing you marks:** {mis['statement']} "
                f"You are not the only one - this is one of the most common wrong models here."
            )
            body.append(f"**Why it is wrong:** {mis['why']}")
            body.append(f"**What to do instead:** {mis['repair']}")
        else:
            body.append(
                "The fastest way to check yourself on this is to ask what would go wrong "
                "if the opposite were true. " + concept["key_ideas"][0].capitalize() + ".")

        if att:
            body.append(
                f"You are {cor}/{att} on this concept. "
                + ("That is the floor of what we need before moving on."
                   if m < core.UNLOCK else
                   "You are past the threshold where I can start pushing you harder.")
            )

        headline = (f"Fix this first: {mis['name']}" if mis
                    else f"{concept['name']}, from the ground up")
        return {
            "headline": headline,
            "hook": concept["analogy"],
            "body": body,
            "key_ideas": concept["key_ideas"],
            "worked": concept.get("worked", []),
            "practice": fb_practice(),
            "modality": modality,
            "concept": cid,
        }

    system = (
        "You are an expert tutor who diagnoses misconceptions. You write tight, concrete, "
        "non-patronising explanations for a university student, and you always follow an "
        "explanation with one short practice problem. You never use filler like "
        "'great question' or 'let's dive in'. You never exceed 160 words of body text. "
        "You return strict JSON only."
    )
    user = f"""Concept: {concept['name']} ({content.subject})
What it means: {concept['summary']}
Author's analogy: {concept['analogy']}
Key ideas the student must end up holding: {concept['key_ideas']}

Student state: {state['correct'][cid]}/{state['attempts'][cid]} correct on this concept,
mastery estimate {core.mastery(state, cid):.2f}, teaching modality '{modality}'.
{f"The student holds this specific misconception: '{mis['statement']}' which is wrong because {mis['why']} Fix: {mis['repair']}" if mis else "No specific misconception diagnosed for this concept."}

Return JSON:
{{"headline": "max 8 words, names the thing being fixed",
  "hook": "one sentence concrete analogy",
  "body": ["3-4 short paragraphs; open by confronting the misconception if there is one",
           "include the student's own numbers where relevant"],
  "worked": ["3-5 steps of a concrete worked example, each one line"],
  "key_takeaway": "one sentence the student should be able to repeat back",
  "practice": {{"problem": "one or two sentences; a concrete problem with specific inputs that tests the mechanism rather than recall, solvable in under two minutes",
               "hint": "one sentence pointing at the mechanism without giving the answer",
               "solution": "the model answer, 1-3 short reasoning steps"}}}}"""

    out = llm.json_call(system, user, fallback, tag=f"explain:{content.id}:{cid}:{modality}:{(mis or {}).get('id','')}")
    out.setdefault("concept", cid)
    out.setdefault("modality", modality)
    out.setdefault("key_ideas", concept["key_ideas"])
    out.setdefault("worked", concept.get("worked", []))
    if not out.get("practice") or not out["practice"].get("problem"):
        out["practice"] = fb_practice()
    return out


# ----------------------------------------------------------------------- grade
def grade(llm, content, state, cid, answer: str) -> dict:
    concept = content.concepts[cid]
    ideas = concept["key_ideas"]

    def fallback() -> dict:
        got = _content_words(answer)
        graded = []
        for idea in ideas:
            want = _content_words(idea)
            if not want:
                continue
            overlap = len(want & got) / len(want)
            hit = overlap >= 0.5 or len(want & got) >= 3
            graded.append({"idea": idea, "hit": hit, "overlap": round(overlap, 2)})
        n = max(1, len(graded))
        score = sum(1 for h in graded if h["hit"]) / n
        # very short answers cannot demonstrate understanding
        words = len(re.findall(r"\w+", answer or ""))
        if words < 12:
            score = min(score, 0.35)
        elif words > 35:
            score = min(0.98, score + 0.05)
        missing = [g["idea"] for g in graded if not g["hit"]]
        return {
            "score": round(score, 2),
            "hits": graded,
            "missing": missing,
            "feedback": _feedback(score, missing),
        }

    system = (
        "You are a strict but fair examiner grading a student's free-text explanation. "
        "You reward correct mechanism and penalise hand-waving. You do not reward length. "
        "Return strict JSON only."
    )
    rubric = "\n".join(f"{i+1}. {idea}" for i, idea in enumerate(ideas))
    user = f"""Concept: {concept['name']}
The student was asked to explain it in their own words.

Rubric - the key ideas a correct explanation must contain:
{rubric}

Student's explanation:
\"\"\"{answer[:2000]}\"\"\"

Grade each rubric idea as hit or not, based on whether the student's explanation
actually expresses that idea (paraphrase counts, buzzwords do not).
Score = fraction of ideas hit, but cap at 0.4 if the explanation is under 12 words.

Return JSON:
{{"hits": [{{"idea": "<rubric idea>", "hit": true, "evidence": "<short quote or 'absent'>"}}],
  "missing": ["<rubric ideas not hit>"],
  "score": 0.0,
  "feedback": "2 sentences: what they clearly understand, and the single most important thing they left out."}}"""

    out = llm.json_call(system, user, fallback, tag=f"grade:{content.id}:{cid}")
    try:
        out["score"] = max(0.0, min(1.0, float(out.get("score", 0.0))))
    except Exception:
        out["score"] = 0.0
    if "hits" not in out:
        out["hits"] = fallback()["hits"]
    if not out.get("feedback"):
        out["feedback"] = _feedback(out["score"], [])
    return out


def _feedback(score: float, missing) -> str:
    if score >= 0.8:
        head = "That is a solid explanation - you have the mechanism, not just the vocabulary."
    elif score >= 0.5:
        head = "You have the shape of it, but the mechanism is still thin in places."
    elif score >= 0.25:
        head = "Partially there, but the core mechanism is missing."
    else:
        head = "This is mostly restating the term rather than explaining it."
    if missing:
        head += f" The most important thing to add: {missing[0]}."
    return head
