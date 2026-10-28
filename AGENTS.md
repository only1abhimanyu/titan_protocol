# MasteryLoop — agent instructions

Adaptive tutoring MVP for Code E Fiesta 2026, problem statement 1.
Team: Titan Protocol. Repo: `only1abhimanyu/titan_protocol` (private).

Read this before editing anything. It records the invariants this codebase was
built around and the bugs that were already found and fixed. Breaking one of
these silently is worse than a compile error, because the demo is what gets
judged and several of these fail only in front of an audience.

---

## What the product is

A student answers adaptive questions. The engine estimates mastery per concept,
traces failures back through a prerequisite graph, names the specific wrong
belief causing them, teaches that gap, and requires a free-text explanation
before mastery moves.

The engine is **deliberate, not an LLM**. Mastery tracing, prerequisite
propagation, misconception diagnosis, item selection and the adaptation policy
are deterministic algorithms in `engine/core.py`. The LLM does exactly two jobs:
personalising an explanation to a named misconception, and grading free text
against a rubric.

## Layout

```
app.py                 FastAPI surface — thin, all logic lives in engine/
engine/core.py         graph, mastery tracing, propagation, diagnosis, selection, policy
engine/tutor.py        explanation + practice generation, teach-back grading
engine/llm.py          provider adapter; every call has a deterministic fallback
engine/session.py      orchestration, persistence, step rendering
engine/store.py        SQLite: users, flashcards, XP ledger, generated topics
engine/security.py     password hashing, session tokens, encrypted key vault
engine/syllabus.py     syllabus -> validated topic (the generation gatekeeper)
engine/flashcards.py   card generation + SM-2 scheduling
engine/gamify.py       XP, levels, streaks, achievements
content/*.json         hand-authored knowledge graphs + item banks
build_content.py       regenerates content/*.json from authoring tables
web/                   front end — no framework, no CDN, no build step
simulate.py            headless student simulation (learner / struggler)
test_api.py            drives the real HTTP routes end to end
test_llm.py            five LLM paths incl. network failure and bad JSON
test_v2.py             auth, key vault, XP, flashcards, syllabus validation, option order
test_ui.js             celebration + goals logic (node, stubbed DOM)
```

## Run and test

```bash
pip install -r requirements.txt
python app.py                      # http://localhost:8000
python build_content.py            # validate the item bank (run after content edits)
python test_api.py                 # full session over real routes
python test_llm.py                 # LLM success / cache / failure / bad JSON / offline
python simulate.py recursion 40 learner     # must reach 9/9 mastered
python simulate.py recursion 22 struggler   # must diagnose, teach and recover
```

**Definition of done for any change:** `build_content.py`, `test_api.py` and
`test_llm.py` all pass, and `simulate.py ... learner` still reaches mastery.
Report the commands you actually ran and their output. Do not claim a check
passed that you did not run.

---

## Hard invariants — do not break these

1. **The answer key never reaches the browser.** `session._render` sends options
   as `{i, text}` only. A test asserts no option carries a `correct` key. Keep
   that assertion; grade on the server.

2. **Every LLM call has a deterministic fallback.** `llm.json_call` takes a
   `fallback` callable and must return something usable with no key, no network,
   a timeout, or malformed JSON. The demo runs fully offline — this is a
   feature, not a safety net. Never add an LLM call without a fallback.

3. **No CDN, no build step in `web/`.** Plain HTML/CSS/JS served by FastAPI.
   Hackathon wifi is unreliable and the demo must work with the network off.
   Do not add React, Tailwind CDN, or a bundler.

4. **Content edits go through `build_content.py`, never hand-edited JSON.** The
   script validates: exactly one correct option per item, four options, every
   misconception id resolves, every prerequisite exists. Hand-editing the JSON
   skips that and the engine fails at runtime instead of build time.

5. **`CALIBRATE` stays `0` for demos.** Item difficulties self-calibrate when
   `CALIBRATE=1`, and repeated runs permanently drift the item bank. This bit us
   once. Leave it off unless you are deliberately recalibrating.

6. **Practice exercises are not graded and must not affect mastery.** Assessment
   is adaptive; practice is rehearsal. Practice carries no misconception tags,
   so it cannot feed diagnosis. Do not wire it into `record_answer` or
   `record_teachback`.

7. **Mastery logic stays deterministic.** If a change requires the LLM to decide
   a mastery number, the change is wrong. The LLM writes prose and grades prose;
   it never moves state.

8. **`next_action` ordering matters.** `pending_verify` is checked before the
   misconception-teach branch. Reversing them reintroduces an infinite
   teach → verify → teach loop that was fixed once already. There is also a
   `teach_count` cap of 3 per concept and a guard against teaching twice in a
   row — all three exist to stop that loop.

9. **Never commit `.env`.** `.env.example` is the committed template and
   contains no secrets. `.gitignore` covers `.env`, `sessions/`, `__pycache__/`
   and `content/_calibration.json`.

10. **Frontend: never use `animation-fill-mode: both` on content.** An entrance
    animation with `both` leaves the element at `opacity: 0` if the animation
    does not advance, which rendered a completely blank card mid-demo. Use
    `.fade { animation: rise .26s ease; }` with no fill mode.

11. **Every route except `/api/health`, `/api/topics`, `/api/auth/*` and `/__demo` requires a
    signed-in user.** Check session ownership (`sess["user_id"]`) before acting on a session id.
    A session id is not a capability.

12. **The API key never leaves the server.** `user_public()` in `app.py` is the only user shape
    that is serialised, and it carries `key_hint` (masked) only. `key_enc` must never appear in a
    response. `test_v2.py` asserts the plaintext key is absent from `/api/settings`.

13. **Option order is shuffled server-side** in `session._render`, seeded by
    `(session_id, item_id)`. Each option keeps its ORIGINAL index in `i`, which is what the client
    sends back. Do not "simplify" this to a fixed order — before the shuffle existed, option A was
    the correct answer in 45 of 45 items, and anyone could have aced the quiz by clicking A.

14. **XP is paid for mastery movement only** (`gamify.xp_for_mastery`). Never award XP per
    question answered, per page view, or per card flipped. A wrong answer must pay exactly 0.

15. **`ML_DEMO=1` enables `/__demo`, which is an authentication bypass.** It exists so a judge can
    see the product without signing up. Never enable it on anything public.

16. **A generated topic must pass `syllabus.validate()` before `syllabus.persist()`.** The
    validator is the only thing standing between an LLM hallucination and a runtime crash.

17. **`data/` holds the SQLite database and the local `.app_secret` file.** It is gitignored.
    Never commit it, and never copy `.app_secret` between machines expecting the same decryption.

18. **The SM-2 preview in `web/app.js` (`previewIntervals`) must mirror
    `engine/flashcards.py` (`review`) exactly.** The grade buttons promise a real
    interval, so drift between the two shows the student a number the server will
    not honour. Change one, change the other, run `node test_ui.js`.

19. **Celebrations diff against localStorage, per user.** Level-ups and achievement
    toasts fire by comparing the current state to what this browser last saw
    (`ml_level_<uid>`, `ml_ach_<uid>`). A first visit seeds silently on purpose — do
    not replace this with a timer or a "show once per session" flag, or the same
    level-up will fire every page load.

20. **The four flashcard grades must produce four different intervals.** They did not,
    originally: hard/good/easy all resolved to `interval x ease` and looked broken.
    If you touch the scheduler, check the previews still differ.

21. **The theme is monochrome by design, and brightness encodes mastery.** The graph's four
    states are distinguished by brightness and fill only - mastered is a white fill with black
    text, gap is a bright outline, developing is a mid grey, untested is a dim dashed outline.
    Do not introduce hue for status, correctness or severity. Success/error in the feedback
    panels use inversion and border style, not green and red.

22. **Syllabus lives on Home, in a modal, with ONE textbox.** Settings holds only the API key.
    Do not reintroduce the three-field form (subject / title / text) - the model names the topic
    from the pasted text. The button is `#openSyl`; the dialog is `#sylModal`.

23. **PDF extraction is two steps on purpose:** `POST /api/syllabus/pdf` returns the extracted
    text, the student sees and can edit it, then `POST /api/syllabus` generates. Never turn a
    dropped PDF straight into a topic the student cannot inspect.

24. **Flashcards are revision cards, not quiz cards.** Front is the concept name (large), back is
    a 2-3 line note plus an optional "Watch out:" line naming the common mistake. No recall /
    cloze / misconception variants, no questions.

## Engine facts worth knowing before you tune anything

- Ability θ per concept, logit scale, clamped to ±4. `mastery = sigmoid(θ)`.
- `P(correct) = sigmoid(θ − d)`; update `θ += K_STUDENT·(y − P)` with
  `K_STUDENT = 0.80`.
- A failure pushes each prerequisite down by `0.18·(1 − mastery)`. This is what
  makes a gap *locatable* rather than merely counted — it is the core idea of
  the product, so do not weaken it to make numbers look tidier.
- `K_TEACHBACK = 1.10`: a graded explanation moves the estimate more than a
  multiple-choice answer, in both directions. Pedagogically deliberate.
- Thresholds: `PASS = 0.70`, `UNLOCK = 0.40`, `WEAK = 0.30`, prior `θ = −0.50`.
- A misconception needs **2** pieces of evidence to be reported. One miss is
  noise; do not lower this to 1 or every session produces a diagnosis.
- Item selection maximises `−|d − θ|` (information gain), not "next unseen".

## Content authoring

Two topics ship: `recursion` (9 concepts, 27 items, 8 misconceptions) and
`normalization` (8 concepts, 18 items, 6 misconceptions). Users can also generate a topic by
pasting a syllabus; those land in `content/<slug>.json` and the `topics` table, and `.gitignore`
keeps them out of version control. Adding an authored topic means
adding a table to `build_content.py` with the same shape — concepts with
prerequisites, named misconceptions, and items whose **distractors are tagged
with the misconception they evidence**. That tagging is what makes diagnosis
possible. An untagged distractor is counted as a generic error and produces no
named diagnosis, which is why some misses look silent.

`engine/` must stay topic-agnostic. Nothing in the engine may mention recursion,
databases, or any subject. A zero in the metrics slide claims there are no topic
names in the engine — keep that true.

## Demo-critical surface — do not rename

- `GET /api/health`, `/api/topics`, `POST /api/session`, `/api/answer`,
  `/api/next`, `/api/teachback`, `GET /api/session/{id}`
- `?autostart=<topic>` skips the topic picker; `?sid=<id>` resumes a session.
  Both are used in the live demo.
- `DEMO_SCRIPT.md` is the rehearsal document. If a UI change breaks a step in
  it, update the script in the same change.

## When the user asks for a change

1. State the user-visible effect in one line before editing.
2. Make the smallest change that achieves it. Do not refactor adjacent code.
3. Run the three test commands and report the real output.
4. List the files you changed and anything you could not verify.
5. If a change would break one of the invariants above, say so and propose the
   alternative instead of doing it.
