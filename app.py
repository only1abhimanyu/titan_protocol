"""MasteryLoop — FastAPI surface.

Thin by design: all product logic lives in engine/. This file is routing,
authentication, and the XP bookkeeping that needs the signed-in user.
"""
from __future__ import annotations

import os
import re
import secrets

from fastapi import (Depends, FastAPI, File, HTTPException, Request, Response,
                     UploadFile)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine import core, flashcards, gamify, security, store, syllabus
from engine.llm import LLM
from engine.session import Registry

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")

app = FastAPI(title="MasteryLoop", version="2.0")

store.init()
default_llm = LLM()          # env-based (GROQ_API_KEY)
reg = Registry(default_llm)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------- auth
def user_or_401(request: Request) -> dict:
    uid = security.read_token(request.cookies.get(security.COOKIE))
    if not uid:
        raise HTTPException(401, "not signed in")
    user = store.get_user(uid)
    if not user:
        raise HTTPException(401, "account no longer exists")
    return user


def set_cookie(resp: Response, uid: str) -> None:
    resp.set_cookie(
        security.COOKIE, security.issue_token(uid),
        max_age=security.TOKEN_TTL, httponly=True, samesite="lax",
        secure=os.environ.get("COOKIE_SECURE", "0") == "1", path="/")


def llm_for(user: dict) -> LLM:
    """Per-user key from the vault wins over the project default."""
    key = security.decrypt_key(user.get("key_enc"))
    return LLM(api_key=key, provider=user.get("key_provider"))


def user_public(u: dict) -> dict:
    """The only user shape that ever leaves the server. No ciphertext, no key."""
    return {
        "id": u["id"], "email": u["email"], "name": u["name"],
        "streak": u.get("streak") or 0, "best_streak": u.get("best_streak") or 0,
        "has_key": bool(u.get("key_enc")),
        "key_provider": u.get("key_provider"),
        "key_hint": u.get("key_hint"),
    }


# -------------------------------------------------------------------- models
class Signup(BaseModel):
    email: str
    name: str
    password: str


class Login(BaseModel):
    email: str
    password: str


class NewSession(BaseModel):
    topic: str
    seed: int | None = None


class Answer(BaseModel):
    session_id: str
    item_id: str
    choice: int


class SessionId(BaseModel):
    session_id: str


class Teachback(BaseModel):
    session_id: str
    text: str


class SyllabusIn(BaseModel):
    subject: str = ""
    title: str = ""
    text: str = ""


class KeyIn(BaseModel):
    key: str


class CardsIn(BaseModel):
    topic: str
    concept: str | None = None


class ReviewIn(BaseModel):
    grade: str


# ------------------------------------------------------------------- health
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "llm_provider": default_llm.provider,
        "llm_model": default_llm.model,
        "llm_available": default_llm.available,
        "topics": list(reg.topics),
        "users": 1 if store.get_user_by_email("x@x.x") else 0,
        "version": "2.0",
    }


# --------------------------------------------------------------------- auth
@app.post("/api/auth/signup")
def signup(body: Signup, response: Response):
    email = (body.email or "").strip().lower()
    name = (body.name or "").strip()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if len(body.password or "") < 8:
        raise HTTPException(400, "Use a password with at least 8 characters.")
    if not name:
        raise HTTPException(400, "Add your name so we know what to call you.")
    if store.get_user_by_email(email):
        raise HTTPException(409, "That email already has an account. Try signing in.")
    pw_hash, pw_salt = security.hash_password(body.password)
    user = store.create_user(email, name, pw_hash, pw_salt)
    set_cookie(response, user["id"])
    return {"user": user_public(user)}


@app.post("/api/auth/login")
def login(body: Login, response: Response):
    user = store.get_user_by_email((body.email or "").strip())
    if not user or not security.verify_password(body.password or "",
                                                user["pw_hash"], user["pw_salt"]):
        raise HTTPException(401, "Email or password is incorrect.")
    store.touch_streak(user["id"])
    set_cookie(response, user["id"])
    return {"user": user_public(store.get_user(user["id"]))}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = Depends(user_or_401)):
    return {"user": user_public(user)}


# -------------------------------------------------------------------- topics
@app.get("/api/topics")
def topics(request: Request):
    uid = security.read_token(request.cookies.get(security.COOKIE))
    owned = {t["id"] for t in store.topics_for(uid)} if uid else set()
    out = []
    for t in reg.topic_list():
        c = reg.topics[t["id"]]
        if c.raw.get("generated") and t["id"] not in owned:
            continue
        out.append(t)
    return {"topics": out}


# ------------------------------------------------------------------ learning
@app.post("/api/session")
def new_session(body: NewSession, user: dict = Depends(user_or_401)):
    if body.topic not in reg.topics:
        raise HTTPException(404, "unknown topic")
    store.touch_streak(user["id"])
    return reg.create(body.topic, user["id"], body.seed, llm=llm_for(user))


@app.get("/api/session/{sid}")
def get_session(sid: str, user: dict = Depends(user_or_401)):
    sess, _ = reg.get(sid)
    if not sess or (sess.get("user_id") and sess["user_id"] != user["id"]):
        raise HTTPException(404, "unknown session")
    return reg.resume(sid)


@app.post("/api/next")
def advance(body: SessionId, user: dict = Depends(user_or_401)):
    sess, _ = reg.get(body.session_id)
    if not sess or (sess.get("user_id") and sess["user_id"] != user["id"]):
        raise HTTPException(404, "unknown session")
    return reg.step(body.session_id, llm=llm_for(user))


@app.post("/api/answer")
def answer(body: Answer, user: dict = Depends(user_or_401)):
    sess, _ = reg.get(body.session_id)
    if not sess or (sess.get("user_id") and sess["user_id"] != user["id"]):
        raise HTTPException(404, "unknown session")
    try:
        out = reg.answer(body.session_id, body.item_id, body.choice, llm=llm_for(user))
    except ValueError as e:
        raise HTTPException(400, str(e))

    fb = out["feedback"]
    xp = gamify.xp_for_mastery(fb["mastery_before"], fb["mastery_after"])
    crossed = fb["mastery_before"] < core.PASS <= fb["mastery_after"]
    if xp:
        store.add_xp(user["id"], "mastery", xp,
                     f"{fb.get('concept', '')} {fb['mastery_before']:.2f}->{fb['mastery_after']:.2f}")
    if crossed:
        store.add_xp(user["id"], "milestone", gamify.XP_MILESTONE,
                     f"{fb.get('concept_name', 'concept')} mastered")
        xp += gamify.XP_MILESTONE
    out["xp_earned"] = xp
    out["xp_total"] = store.total_xp(user["id"])
    return out


@app.post("/api/teachback")
def teachback(body: Teachback, user: dict = Depends(user_or_401)):
    sess, _ = reg.get(body.session_id)
    if not sess or (sess.get("user_id") and sess["user_id"] != user["id"]):
        raise HTTPException(404, "unknown session")
    try:
        out = reg.teachback(body.session_id, body.text, llm=llm_for(user))
    except ValueError as e:
        raise HTTPException(400, str(e))
    g = out["grade"]
    xp = gamify.xp_for_teachback(g["score"])
    if xp:
        store.add_xp(user["id"], "teachback", xp, f"{g['concept_name']} scored {g['score']}")
    out["xp_earned"] = xp
    out["xp_total"] = store.total_xp(user["id"])
    return out


# ------------------------------------------------------------------ syllabus
@app.post("/api/syllabus")
def make_topic(body: SyllabusIn, user: dict = Depends(user_or_401)):
    if not (body.text.strip() or body.title.strip()):
        raise HTTPException(400, "Paste your syllabus or give the topic a name.")
    if len(body.text) > 20000:
        raise HTTPException(400, "That is more than 20,000 characters - trim it a little.")
    topic, problems, source = syllabus.generate(
        llm_for(user), body.subject, body.title, body.text)
    if not topic:
        raise HTTPException(422, {"problems": problems, "source": source,
                                  "hint": ("Add an API key in Settings, or paste a more "
                                           "detailed syllabus with named topics.")})
    path = syllabus.persist(topic, user["id"], store)
    reg.load_topic(path)
    store.add_xp(user["id"], "syllabus", 25, topic["title"])
    return {
        "topic": {"id": topic["id"], "title": topic["title"], "subject": topic["subject"],
                  "concepts": len(topic["concepts"]), "items": len(topic["items"]),
                  "misconceptions": len(topic.get("misconceptions") or [])},
        "source": source,
    }


@app.post("/api/syllabus/pdf")
async def syllabus_pdf(file: UploadFile = File(...), user: dict = Depends(user_or_401)):
    """Extract text from a dropped PDF and hand it back for review before generating.

    Deliberately two steps: the student sees and can edit what was read, instead of
    a PDF being silently turned into a topic they cannot inspect.
    """
    import io

    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(400, "That file is not a PDF.")
    raw = await file.read()
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(400, "That PDF is larger than 12 MB.")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        pages = [pg.extract_text() or "" for pg in reader.pages[:25]]
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(400, f"Could not read that PDF ({type(e).__name__}).")
    text = "\n".join(pages).strip()
    if len(text) < 40:
        raise HTTPException(
            422, "That PDF has no selectable text - it is probably a scan. "
                 "Paste the text instead, or export a text-based PDF.")
    return {"text": text[:18000], "pages": len(pages), "chars": len(text),
            "truncated": len(text) > 18000}


# --------------------------------------------------------------- flashcards
@app.get("/api/flashcards")
def list_cards(user: dict = Depends(user_or_401)):
    due = store.due_cards(user["id"], limit=40)
    stats = store.card_stats(user["id"])
    return {
        "due": [{k: c[k] for k in ("id", "front", "back", "concept", "kind", "topic",
                                   "ease", "interval", "reps")} for c in due],
        "stats": stats,
    }


@app.post("/api/flashcards/generate")
def gen_cards(body: CardsIn, user: dict = Depends(user_or_401)):
    content = reg.topics.get(body.topic)
    if not content:
        raise HTTPException(404, "unknown topic")
    concept_ids = [body.concept] if body.concept else list(content.concepts)
    concept_ids = [c for c in concept_ids if c in content.concepts]

    diagnosed: list[str] = []
    sess = reg.latest_for(user["id"], body.topic)
    if sess:
        diagnosed = list(sess["state"].get("mis", {}).keys())
        diag = core.diagnose(content, sess["state"])
        if diag:
            diagnosed.append(diag["misconception_id"])
    diagnosed = sorted(set(diagnosed))

    cards, source = flashcards.generate(llm_for(user), content, concept_ids, diagnosed)
    added = store.upsert_cards(user["id"], cards)
    stats = store.card_stats(user["id"])
    return {"added": added, "generated": len(cards), "source": source,
            "targeted_misconceptions": diagnosed, "stats": stats}


@app.post("/api/flashcards/{cid}/review")
def review_card(cid: str, body: ReviewIn, user: dict = Depends(user_or_401)):
    card = store.get_card(user["id"], cid)
    if not card:
        raise HTTPException(404, "unknown card")
    upd = flashcards.review(card, body.grade)
    store.update_card(cid, **upd)
    xp = 0
    if body.grade != "again":
        xp = store.add_xp(user["id"], "flashcard", gamify.XP_REVIEW, card["front"][:60])
        xp = gamify.XP_REVIEW
    else:
        xp = 0
    return {"next_in": flashcards.human_interval(upd["interval"]),
            "due": upd["due"], "reps": upd["reps"], "ease": upd["ease"],
            "xp_earned": xp, "xp_total": store.total_xp(user["id"])}


# ------------------------------------------------------------------ settings
@app.get("/api/settings")
def get_settings(user: dict = Depends(user_or_401)):
    return {
        "has_key": bool(user.get("key_enc")),
        "provider": user.get("key_provider"),
        "masked": user.get("key_hint"),
        "default_provider": default_llm.provider,
        "default_available": default_llm.available,
        "providers": {k: {"label": v["label"], "prefix": v["prefix"], "help": v["help"]}
                      for k, v in security.PROVIDERS.items()},
        "encryption": "Fernet (AES-128-CBC + HMAC) under a server-side secret",
    }


@app.post("/api/settings/key")
def save_key(body: KeyIn, user: dict = Depends(user_or_401)):
    ok, msg, provider = security.validate_key(body.key)
    if not ok:
        raise HTTPException(400, msg)
    enc = security.encrypt_key(body.key.strip())
    store.set_user_key(user["id"], enc, provider, security.mask_key(body.key.strip()))
    return {"ok": True, "provider": provider,
            "masked": security.mask_key(body.key.strip())}


@app.delete("/api/settings/key")
def clear_key(user: dict = Depends(user_or_401)):
    store.set_user_key(user["id"], None, None, None)
    return {"ok": True}


@app.post("/api/settings/key/test")
def test_key(user: dict = Depends(user_or_401)):
    """Prove the stored key actually works, instead of failing silently later."""
    key = security.decrypt_key(user.get("key_enc"))
    if not key:
        raise HTTPException(400, "No key saved - the project default is in use.")
    llm = LLM(api_key=key, provider=user.get("key_provider"))
    out = llm.json_call(
        "Reply with JSON only.",
        'Return exactly {"ok": true}',
        lambda: {"ok": False}, tag="keytest")
    return {"works": out.get("ok") is True, "source": out.get("source"),
            "provider": llm.provider, "error": llm.last_error}


# ----------------------------------------------------------- dashboard data
@app.get("/api/me/summary")
def summary(user: dict = Depends(user_or_401)):
    uid = user["id"]
    xp = store.total_xp(uid)
    cards = store.card_stats(uid)
    sessions = reg.user_sessions(uid)
    generated = [t for t in store.topics_for(uid) if t.get("owner") == uid]
    best_tb = 0.0
    for s in sessions:
        for tb in s.get("teachback") or []:
            best_tb = max(best_tb, float(tb.get("score") or 0))
    return {
        "user": user_public(user),
        "game": gamify.summary(user, xp, store.xp_breakdown(uid), store.recent_xp(uid, 40)),
        "cards": cards,
        "sessions": sessions,
        "achievements": gamify.achievements(
            sessions[0] if sessions else None,
            user, best_tb, len(generated), cards["total"]),
        "llm": {"provider": llm_for(user).provider,
                "available": llm_for(user).available,
                "using_own_key": bool(user.get("key_enc"))},
    }


# ------------------------------------------------------------- demo entry
# Local convenience, off unless ML_DEMO=1. Creates/uses a demo account and signs
# in, so a judge can see the product without a signup form. Never enable this on
# a public deployment - it is an authentication bypass by design.
@app.get("/__demo")
def demo_login():
    if os.environ.get("ML_DEMO") != "1":
        raise HTTPException(404, "not found")
    email = "demo@masteryloop.local"
    user = store.get_user_by_email(email)
    if not user:
        pw_hash, pw_salt = security.hash_password(secrets.token_urlsafe(24))
        user = store.create_user(email, "Demo Student", pw_hash, pw_salt)
    store.touch_streak(user["id"])
    # the cookie must be set on the response we actually return
    resp = RedirectResponse("/#/app", status_code=302)
    set_cookie(resp, user["id"])
    return resp


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    # Local demo binds to loopback so Windows does not throw a firewall prompt;
    # a PaaS that sets PORT needs 0.0.0.0 to accept traffic.
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", "8000")))
