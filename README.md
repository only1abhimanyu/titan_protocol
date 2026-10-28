# MasteryLoop

**The AI that learns how you learn.** A working adaptive tutor for Code E Fiesta 2026, Problem Statement 1.

MasteryLoop does not ask you questions and count your score. It builds a model of what you
know, traces every wrong answer back through a prerequisite graph to the concept that is
actually broken, names the specific wrong belief causing it, teaches that gap, and then makes
you explain it in your own words before it will move you forward.

---

## Why this is not a chatbot

| A quiz app | MasteryLoop |
|---|---|
| Marks each question right or wrong | Updates a per-concept ability estimate with an Elo/IRT update |
| "You scored 6/10" | "You hold the belief that *recursion stops on its own* — 3 answers prove it" |
| Treats every wrong answer as its own failure | Propagates failure to prerequisite concepts, so a gap is **located**, not just counted |
| Fixed question order | Picks the item with maximum information gain given your current estimate (real CAT) |
| Same explanation for everyone | The explanation confronts *your* named misconception, using your own numbers |
| Correct answer = mastery | A correct answer you cannot explain does not move mastery (teach-back gate) |

The LLM is **not** the engine. Mastery estimation, prerequisite propagation, misconception
diagnosis, item selection and the adaptation policy are deterministic algorithms — auditable,
reproducible, instant. The LLM is used only where a template genuinely cannot do the job:
personalising an explanation to a specific misconception, and grading free text against a rubric.

---

## What's new in v2

- **Accounts.** Email + password (scrypt, per-user salt), HMAC-signed HttpOnly session cookies.
- **Paste a syllabus, get a tutor.** A model builds the concept graph, misconception list and
  tagged item bank for any topic you paste. Validated hard before it is persisted — a malformed
  topic is rejected with the specific problems, never silently loaded.
- **AI flashcards with spaced repetition.** Cards are generated from the misconceptions your
  session actually diagnosed, not from generic definitions. SM-2 scheduling with short learning
  steps so reviews come back the same day.
- **Bring your own API key.** The project ships with a default (Groq). Add your own in Settings
  and it is Fernet-encrypted before it touches the database, masked on read, and never sent back
  to the browser.
- **Gamification that cannot be farmed.** XP is paid per point of mastery gained, not per question
  answered. A wrong answer pays nothing and costs mastery. Grinding flashcards pays 2 XP a card.
  Every award is an append-only ledger row, so the profile can be audited event by event.
- **Six pages** in the design language from the Margin concept: landing, login, home, learn,
  flashcards, profile, settings — with a collapsible sidebar.

## Run it

```bash
pip install -r requirements.txt

export GROQ_API_KEY=gsk_...        # the project default; works fully without it
export APP_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")

python app.py            # or: uvicorn app:app --port 8000
```

Open <http://localhost:8000>.

For a judge-friendly entry with no signup form: `ML_DEMO=1 python app.py`, then open
`http://localhost:8000/__demo`. **`ML_DEMO` is an authentication bypass — never enable it on a
public deployment.**

`./run.sh` does the same thing with a venv.

---

## The 60-second tour

1. Pick **Recursion**.
2. Answer the questions. Answer a couple **deliberately wrong** — pick the option that sounds
   plausible but sloppy.
3. Watch the graph. Wrong answers push mastery *down the prerequisite chain*, so concepts you
   were never asked about start turning red. That is the propagation step.
4. After two errors that point at the same wrong idea, the diagnosis panel names it, shows the
   confidence, and lists the exact questions that evidenced it.
5. The engine switches to a targeted explanation of the **root cause** concept.
6. You explain it back. It is graded against a rubric of key ideas. Mastery only moves if your
   explanation actually lands.
7. The graph updates, and the next question is chosen for the new state.

---

## How the engine works

**Knowledge graph** (`content/*.json`). Each topic is decomposed into concepts with prerequisite
edges — recursion is 9 concepts from "how function calls work" up to "tail recursion". Every
item is tagged with the *misconception* each distractor evidences. That tagging is what makes
diagnosis possible, and it is hand-authored, not generated.

**Mastery tracing** (`engine/core.py`). Each (student, concept) pair holds an ability estimate
θ on a logit scale. For an item of difficulty d, expected correctness is
`P = sigmoid(θ − d)`, and the answer moves θ by `K·(y − P)`. Item difficulties self-calibrate
the same way (opt-in via `CALIBRATE=1`, off by default so demo runs stay clean).

**Prerequisite propagation.** A failure at a concept is weak evidence that its foundations are
shaky, so θ for each prerequisite is pushed down proportionally to how weak the concept is.
A confident pass nudges prerequisites up. This is why the gap *moves up the graph* — the thing
that makes a gap locatable rather than merely counted.

**Diagnosis.** A misconception needs ≥2 pieces of evidence to be reported. Confidence scales
with evidence count and how many concepts the error spans. The **root cause** is the weakest
node in the prerequisite closure of everything the misconception touches.

**Item selection.** Among reachable unseen items, maximise `−|d − θ|` — the item whose
difficulty sits closest to your current ability. A question you are certain to pass, or certain
to fail, carries almost no information. The UI states this reason on every question.

**Policy.** Outstanding teach-back → verify. Untaught diagnosed misconception → teach it.
Taught misconception that produced fresh evidence → *"explanation did not land"*, re-teach with a
different modality (analogy → worked example → Socratic). Repeated failure on one concept →
switch modality. Concept below 0.30 → repair upstream. Otherwise probe.

**Teach-back.** Free text, graded against the concept's rubric of key ideas. This is weighted
**more heavily** than a multiple-choice answer (generation effect) — in both directions. Explain
it badly and mastery falls.

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | provider, model, topics, session count |
| `GET /api/topics` | available topics with counts |
| `POST /api/session` | `{topic, seed?}` → first step + full state |
| `POST /api/answer` | `{session_id, item_id, choice}` → feedback + next step |
| `POST /api/next` | advance with no input (after reading an explanation) |
| `POST /api/teachback` | `{session_id, text}` → grade + next step |
| `GET /api/session/{id}` | resume a session exactly where it was |

`?sid=<session_id>` in the URL resumes in the browser. `?autostart=recursion` skips the picker.

---

## Layout

```
app.py                 FastAPI surface (thin - all logic is in engine/)
engine/core.py         graph, mastery tracing, propagation, diagnosis, selection, policy
engine/tutor.py        explanation + practice generation, teach-back grading (+ fallbacks)
engine/llm.py          provider adapter (Groq default); every call has a deterministic fallback
engine/session.py      orchestration, persistence, step rendering
engine/store.py        SQLite: users, flashcards, XP ledger, generated topics
engine/security.py     password hashing, session tokens, the encrypted key vault
engine/syllabus.py     syllabus -> validated topic (the generation gatekeeper)
engine/flashcards.py   card generation + SM-2 scheduling
engine/gamify.py       XP, levels, streaks, achievements
content/*.json         hand-authored knowledge graphs + item banks
build_content.py       regenerates content/*.json from the authoring tables
web/                   the whole front end - no framework, no CDN, works offline
simulate.py            headless student simulation (learner / struggler)
test_api.py            drives the real HTTP routes end to end
test_llm.py            proves all five LLM paths including failure
test_v2.py             auth, vault, XP, flashcards, syllabus validation, option order
```

## Tests

```bash
python build_content.py      # validate the item bank integrity
python test_v2.py            # auth, key vault, XP, flashcards, syllabus, option order
python test_api.py           # full session over real routes
python test_llm.py           # LLM success / cache / failure / bad-JSON / offline
python simulate.py recursion 30 learner
python simulate.py recursion 22 struggler
```

`test_api.py` asserts that the correct answer is never sent to the client before the student
answers.

---

## Adding a topic

Add a table to `build_content.py` with the same shape (concepts + prereqs + misconceptions +
items with distractor tags), run it, and the topic appears in the picker. The engine is
topic-agnostic — nothing in `engine/` knows what recursion is. Two topics ship as proof:
Recursion (DSA) and DBMS Normalization.

## Honest limitations

- The item bank is small (27 + 18). Adaptive testing wants 100+ items per topic; the engine is
  built for it, the content is the bottleneck.
- Misconception diagnosis depends on distractor tagging. Untagged distractors are counted as
  generic errors, which is why some misses produce no named diagnosis.
- Mastery is a model, not a measurement. The numbers are defensible and they move for
  defensible reasons, but no 45-item bank can certify mastery of a topic.
- Teach-back grading via the lexical fallback is crude. With an LLM key it is markedly better.
