"""Gamification where the score is learning, not activity.

The design thesis, and the thing that separates this from a quiz app's badge
shelf: **XP is a function of mastery movement, not of questions answered.**

Precisely what that does and does not claim. A correct answer pays in proportion
to how much it moved the estimate; a wrong answer pays nothing and *costs*
mastery, so guessing nets you nothing over a run - the estimate stays flat while
your XP barely moves. A single lucky guess does pay, because one correct answer
is genuinely weak evidence and the model says so.

The real anti-gaming gate is the teach-back, not the XP: a correct answer you
cannot explain does not move mastery at all. Grinding flashcards pays 2 XP a card
on purpose - reviewing is rehearsal, not learning.

Every award is written to an append-only ledger (`xp_events`), so a judge can ask
"where did those 340 points come from" and get a real answer per event.
"""
from __future__ import annotations

LEVELS = [
    (1, "Seed", 0), (2, "Sprout", 60), (3, "Apprentice", 240), (4, "Practitioner", 540),
    (5, "Adept", 960), (6, "Scholar", 1500), (7, "Master", 2160), (8, "Sage", 2940),
]

XP_PER_MASTERY = 140      # per 1.0 of mastery gained
XP_TEACHBACK = 45         # scaled by rubric score
XP_MILESTONE = 60         # first time a concept crosses PASS
XP_REVIEW = 2             # a flashcard review - deliberately small


def level_for(xp: int) -> dict:
    lvl, title, floor = LEVELS[0]
    nxt = None
    for i, (n, t, th) in enumerate(LEVELS):
        if xp >= th:
            lvl, title, floor = n, t, th
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    span = (nxt[2] - floor) if nxt else 0
    progress = ((xp - floor) / span) if span else 1.0
    return {
        "level": lvl, "title": title, "xp": xp,
        "floor": floor,
        "next_title": nxt[1] if nxt else None,
        "next_at": nxt[2] if nxt else None,
        "to_next": max(0, (nxt[2] - xp)) if nxt else 0,
        "progress": round(min(1.0, max(0.0, progress)), 3),
    }


def xp_for_mastery(before: float, after: float) -> int:
    """Only upward movement pays. This is the whole point."""
    return int(round(max(0.0, after - before) * XP_PER_MASTERY))


def xp_for_teachback(score: float) -> int:
    return int(round(max(0.0, score) * XP_TEACHBACK)) if score >= 0.55 else 0


ACHIEVEMENTS = [
    ("first_blood", "First diagnosis", "A named misconception was found in your work."),
    ("gap_closed", "Gap closed", "A concept moved out of the red."),
    ("first_mastery", "Concept mastered", "One concept reached mastery."),
    ("three_mastered", "Three in the bag", "Three concepts at mastery in one topic."),
    ("teachback_pro", "Explained it back", "A teach-back scored 80 or better."),
    ("streak3", "Three days running", "Three consecutive days of study."),
    ("syllabus", "Your own syllabus", "A topic generated from pasted syllabus text."),
    ("deck", "Deck started", "Flashcards generated from your gaps."),
]


def achievements(session: dict | None, user: dict | None,
                 teachback_best: float, generated_topics: int, card_total: int) -> list[dict]:
    """Computed from the live session summary rather than stored, so they never drift."""
    have: dict[str, bool] = {}
    metrics = (session or {}).get("metrics") or {}
    have["first_blood"] = bool((session or {}).get("diagnosis"))
    mastered = len(metrics.get("mastered") or [])
    have["first_mastery"] = mastered >= 1
    have["three_mastered"] = mastered >= 3
    gaps = metrics.get("gaps") or []
    developing = metrics.get("developing") or []
    have["gap_closed"] = len(developing) > 0 and len(gaps) == 0 and metrics.get("answers", 0) > 2
    have["teachback_pro"] = teachback_best >= 0.8
    have["streak3"] = (user or {}).get("streak", 0) >= 3
    have["syllabus"] = generated_topics > 0
    have["deck"] = card_total > 0
    return [{"id": i, "name": n, "desc": d, "earned": have.get(i, False)}
            for i, n, d in ACHIEVEMENTS]


def summary(user: dict, xp: int, breakdown: list[dict], recent: list[dict]) -> dict:
    lv = level_for(xp)
    return {
        "xp": xp,
        "level": lv["level"],
        "title": lv["title"],
        "next_title": lv["next_title"],
        "to_next": lv["to_next"],
        "progress": lv["progress"],
        "streak": user.get("streak", 0),
        "best_streak": user.get("best_streak", 0),
        "breakdown": breakdown,
        "recent": recent,
        "rule": "XP is paid per point of mastery gained, not per question answered.",
    }
