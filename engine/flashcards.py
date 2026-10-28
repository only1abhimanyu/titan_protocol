"""AI flashcards with SM-2 spaced repetition.

Two things make these different from a generic AI flashcard app:

1. Cards are generated from the topic's *named misconceptions*, not just its
   definitions. A card whose front is the wrong belief and whose back is why it
   fails attacks the thing the diagnosis already found.
2. The deterministic fallback builds real cards from authored content, so
   flashcards work with no API key at all.

Reviews never touch mastery. Assessment is the adaptive part; card review is
rehearsal. Keeping those separate is what stops the mastery number from being
inflatable by grinding cards.
"""
from __future__ import annotations

import hashlib

MINUTE, DAY = 60.0, 86400.0

GRADES = {"again": 1, "hard": 3, "good": 4, "easy": 5}


def _card(topic: str, concept: str, kind: str, front: str, back: str,
          source_mis: str | None = None) -> dict:
    return {"topic": topic, "concept": concept, "kind": kind,
            "front": front.strip(), "back": back.strip(), "source_mis": source_mis}


def _concepts_for(content, mid: str) -> list[str]:
    """Misconceptions are not always tagged with concepts; infer from the items."""
    out = []
    for it in content.items.values():
        for o in it["options"]:
            if o.get("misconception") == mid and it["concept"] not in out:
                out.append(it["concept"])
    return out


def _trap_for(content, cid: str, diagnosed: list[str]) -> str | None:
    """The one mistake worth a line on a revision card."""
    pool = []
    for mid, mis in content.misconceptions.items():
        if cid in (mis.get("concepts") or _concepts_for(content, mid)):
            pool.append((mid, mis))
    if not pool:
        return None
    # prefer a misconception this student actually holds
    pool.sort(key=lambda t: (t[0] not in diagnosed,))
    mid, mis = pool[0]
    st = (mis.get("statement") or "").strip().rstrip(".")
    if not st:
        return None
    return st[0].lower() + st[1:] if st else None


def _shorten(text: str, limit: int = 240) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "\u2026"


def _from_content(content, concept_ids: list[str], diagnosed: list[str]) -> list[dict]:
    """One revision card per concept: the name on the front, 2-3 lines on the back.

    No model needed, so a deck always exists. The note is the authored summary
    trimmed to a revision length, plus the common trap for that concept.
    """
    cards = []
    for cid in concept_ids:
        c = content.concepts.get(cid)
        if not c:
            continue
        note = _shorten(c.get("summary") or "")
        trap = _trap_for(content, cid, diagnosed)
        if trap:
            note = (note + "\n\nWatch out: " + trap + ".") if note else ("Watch out: " + trap + ".")
        cards.append(_card(content.id, cid, "revision", c["name"], note))
    return cards


SYSTEM = ("You write short revision notes for a student the night before an exam. "
          "Each note states the mechanism, not the definition. No preamble, no filler. "
          "Strict JSON only.")


def generate(llm, content, concept_ids: list[str], diagnosed: list[str]) -> tuple[list[dict], str]:
    """LLM cards, merged with the deterministic set so the count is never zero."""
    base = _from_content(content, concept_ids, diagnosed)

    targets = [content.concepts[c] for c in concept_ids if c in content.concepts]
    if not targets:
        return base, "template"

    detail = "\n".join(
        f"- {c['name']}: {c['summary']}" for c in targets[:8])
    mis = "\n".join(
        f"- {m['name']}: the student believes \u201c{m['statement']}\u201d"
        for mid, m in content.misconceptions.items() if mid in diagnosed) or "(none)"

    user = f"""Topic: {content.title} ({content.subject})

Concepts to revise:
{detail}

Mistakes this student actually makes:
{mis}

Write one revision card per concept. For each, a note of 2-3 lines (max 50 words) that
states how it actually works. Where a concept has a mistake listed above, add a final
sentence starting "Watch out:" naming it. Do not write quiz questions.

Return JSON:
{{"cards": [{{"concept": "<exact concept name from the list>",
             "note": "2-3 line revision note"}}]}}"""

    def fallback() -> dict:
        return {"cards": []}

    out = llm.json_call(SYSTEM, user, fallback, tag=f"cards:{content.id}:{','.join(concept_ids)}")
    extra = []
    name_to_id = {c["name"].lower(): cid for cid, c in content.concepts.items()}
    for c in out.get("cards") or []:
        if not isinstance(c, dict) or not c.get("note"):
            continue
        cid = name_to_id.get((c.get("concept") or "").lower())
        if not cid:
            continue
        extra.append(_card(content.id, cid, "revision",
                           content.concepts[cid]["name"], _shorten(str(c["note"]), 300)))

    merged, seen = [], set()
    for c in base + extra:
        h = hashlib.sha256(c["front"].lower().encode()).hexdigest()[:16]
        if h in seen:
            continue
        seen.add(h)
        merged.append(c)
    return merged[:24], out.get("source", "template")


# ------------------------------------------------------------------- SM-2
def review(card: dict, grade: str) -> dict:
    """Anki-style SM-2 with short learning steps so a demo can see cards return.

    `interval` is stored in SECONDS. reps 1 -> 10 minutes, reps 2 -> 1 day,
    then interval x ease. Lapse resets to the 10-minute step.
    """
    q = GRADES.get(grade, 4)
    ease = float(card.get("ease") or 2.5)
    interval = float(card.get("interval") or 0)
    reps = int(card.get("reps") or 0)
    lapses = int(card.get("lapses") or 0)

    if q < 3:                                   # "again" - back to the learning step
        reps = 0
        lapses += 1
        interval = 10 * MINUTE
    elif q == 3:                                # "hard" - barely moves
        reps += 1
        interval = 10 * MINUTE if reps == 1 else max(interval * 1.2, DAY)
    elif q == 4:                                # "good" - the normal step
        reps += 1
        if reps == 1:
            interval = DAY
        elif reps == 2:
            interval = 3 * DAY
        else:
            interval = max(interval * ease, DAY)
    else:                                       # "easy" - jumps ahead
        reps += 1
        interval = 4 * DAY if reps == 1 else max(interval * ease * 1.3, 4 * DAY)

    ease = ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    ease = max(1.3, min(3.0, round(ease, 3)))

    import time
    return {"ease": ease, "interval": round(interval, 1), "reps": reps,
            "lapses": lapses, "due": time.time() + interval}


def human_interval(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min"
    if seconds < 86400 * 1.5:
        return f"{round(seconds / 3600)} hr"
    return f"{round(seconds / 86400)} d"
