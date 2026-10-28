# 3-Minute Demo Script

Total budget: **3:00**. Rehearse this twice. The demo is the product.

---

## Before you walk up (60 seconds before)

```bash
cd masteryloop
export GEMINI_API_KEY=...          # optional; it works without it
python app.py
```

- Open `http://localhost:8000/?autostart=recursion` — skips the picker, one less click.
- **Zoom the browser to 110%.** Judges are 3 metres away.
- Close every other tab. Turn off notifications. Disable the laptop's sleep.
- Have a **second terminal** ready with `python simulate.py recursion 22 struggler` — this is
  your fallback if the browser misbehaves.
- Check the badge top-right. `gemini · ...` = live LLM. `offline engine` = template content.
  **Either is fine.** Do not apologise for offline mode; the engine is the product, the LLM is
  a garnish.

---

## 0:00 — 0:20 | The problem, in one sentence

> "Every student gets the same lecture, the same notes, and the same practice set — and when
> they get a question wrong, every system on the market tells them the same useless thing:
> *incorrect*.
>
> We built the thing that tells you **which wrong idea you're holding**."

Do not say "we built an AI tutor." That phrase makes judges think chatbot. Say what it does.

---

## 0:20 — 1:05 | Make it fail, on purpose

> "I'm going to answer these badly, deliberately. Watch the graph, not the questions."

Answer **wrong** on purpose. Pick the plausible-but-sloppy option — the one that *sounds* right.

- Q1, Q2: answer correctly. Two green bars move. Say nothing yet.
- Q3 (base case): answer **wrong** — "It cannot happen; a base case guarantees termination".
- Q4: answer **wrong** again on the same idea.

**This is the moment.** Point at the graph:

> "I have never been asked a question about *the call stack*. But it just went from grey to
> amber — because failing at *base case* is evidence my foundations are shaky. The system
> doesn't just record the miss, it **locates** it."

---

## 1:05 — 1:35 | The diagnosis

The **Diagnosis** panel fires. Read it out loud, slowly:

> "**'Recursion stops on its own.'** Confidence 69%. Root cause: *the base case*.
> And here are the two questions that prove it."

> "No quiz app on the planet tells you that. It tells you 4 out of 10. This tells you the
> sentence in your head that is wrong."

---

## 1:35 — 2:15 | Teach, then make me prove it

Click through to the explanation.

> "Notice what it's doing: it's not explaining recursion. It's confronting **my** specific
> wrong belief — *'recursion terminates as long as it looks like it's getting smaller'* — and
> telling me exactly why that's false."

Now the teach-back. Type something **deliberately incomplete** — one sentence, missing the
mechanism:

> "a base case stops the recursion"

Submit. Score lands low.

> "I got the question right a minute ago. It doesn't care. **A correct answer I can't explain
> isn't mastery** — so mastery doesn't move."

Now type a real explanation:

> "A base case is a branch that returns without calling itself. Every recursive path must be
> able to reach it, otherwise the frames keep stacking until the stack overflows."

Submit. Score jumps, mastery moves hard.

> "Explaining it in your own words is stronger evidence than picking option B. So it moves the
> estimate further — in both directions."

---

## 2:15 — 2:45 | Show it is a platform, not a demo

Switch to the **DBMS Normalization** topic. Let the first question appear. Do not answer it.

> "Same engine. It knows nothing about recursion — no code mentions recursion. The knowledge
> graph, the misconceptions, the items, all of it is content. This is a platform, and the
> second topic took twenty minutes to author."

If you have 10 spare seconds, hit `Ctrl+C` in a terminal and restart the server, then reopen
the session URL:

> "Session state is persisted. Nothing here is a scripted animation."

---

## 2:45 — 3:00 | Close

> "Six hours, two topics, 45 items, 14 named misconceptions, and a mastery model that moves
> for reasons we can show you in a log.
>
> The bigger vision is the brief's own: a personal tutor that adapts to you, for a student
> whose family cannot buy one. This runs on a free API key and a five-year-old laptop.
>
> The engine is deterministic and auditable — every number on that screen traces back to an
> answer. Ask us anything."

Stop talking. Let them ask.

---

## Questions you will get, and the answers

**"Isn't this just an LLM with a prompt?"**
> "No — and that's deliberate. The mastery model, the propagation, the diagnosis and the item
> selection are all deterministic algorithms you can read in `engine/core.py`. The LLM only
> writes the explanation and grades the free text. Kill the network and the whole thing still
> runs — there's a test for exactly that."

**"How do you know the mastery numbers mean anything?"**
> "They're an Elo/IRT estimate — the same family used in real computerised adaptive testing.
> They're a model, not a measurement, and we're honest about that: 27 items cannot certify
> mastery. But they move for defensible reasons, and every move is logged."

**"What stops it from being gamed?"**
> "The teach-back. You can guess a multiple-choice question; you can't bluff an explanation
> against a rubric. And a bad explanation moves mastery *down*."

**"Where does the content come from?"**
> "Hand-authored, and that's the moat. Every distractor is tagged with the misconception it
> evidences — that tagging is what makes diagnosis possible. You can't generate it reliably;
> you author it once and reuse it for every student."

**"Does it scale?"**
> "The engine is stateless per request and the session is a JSON document. The bottleneck is
> content, not compute — which is a good problem, because content is authorable once and
> amortised over every student."

---

## If something breaks

| Symptom | Do this |
|---|---|
| Blank card | Hard-refresh (`Ctrl+Shift+R`). Never debug live. |
| Server died | Second terminal: `python simulate.py recursion 22 struggler` — narrate the trace output instead. It shows the same diagnosis, propagation and recovery. |
| LLM slow / erroring | Badge shows `offline engine`. Say "running on authored content" and keep going. Nothing else changes. |
| Diagnosis panel empty | You need **two** errors on the *same* misconception. Answer one more question wrong on the same idea. |
| Judge wants to try it | Hand them the keyboard on the normalization topic — it's a fresh session and it will work. |

**Never** say "it usually works." Say "let me show you the engine trace instead" and switch to
the terminal. The trace is more convincing than the UI anyway.
