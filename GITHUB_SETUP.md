# Putting MasteryLoop on a private GitHub repo

Written for a repo owned by `only1abhimanyu` named `titan_protocol`.

---

## Before you click "Create repository"

Two settings on the GitHub new-repo screen decide whether your first push is
clean or a mess:

| Field | Set it to | Why |
|---|---|---|
| **Add README** | **Off** | With it On, GitHub creates a commit you don't have. Your first `git push` is then rejected as unrelated histories and you have to fight it with `--force` or `--allow-unrelated-histories`. With it Off the repo is empty and the push just works. This project already has its own README. |
| **Add .gitignore** | **None** | Ours is included in this project. GitHub's Python template doesn't know about `sessions/` or `content/_calibration.json`. |
| **Add license** | **No license** | A private hackathon repo doesn't need one, and adding MIT now is hard to walk back if you later want to keep the work closed. Add one deliberately, later, if you decide to. |
| **Copilot prompt** | leave empty | Not needed. |

Everything else on that screen is fine: owner, name `titan_protocol`, Private,
and the description.

---

## Push it (terminal)

```bash
# 1. unzip
unzip MasteryLoop.zip
cd masteryloop

# 2. start the repo
git init -b main

# 3. see what will be committed BEFORE you commit it
git add .
git status
```

**Read that `git status` output.** You should see roughly 25 files and *not*
see `.venv/`, `__pycache__/`, `sessions/`, or `.env`. If you see `.env` in that
list, stop — you are about to publish an API key.

```bash
# 4. commit
git config user.name  "Your Name"
git config user.email "you@example.com"
git commit -m "MasteryLoop: adaptive tutor with prerequisite diagnosis and teach-back verification

Problem statement 1, Code E Fiesta 2026.

Engine (deterministic, no LLM in the loop):
- Elo/IRT mastery tracing per concept, with prerequisite propagation so a
  failure at one concept lowers belief in its foundations
- Misconception diagnosis: two tagged errors on one belief produce a named
  finding with confidence and root-cause concept
- Information-gain item selection (computerised adaptive testing)
- Policy: repair upstream below 0.30, switch teaching modality on repeat
  failure, re-teach when an explanation did not land

LLM is used only for personalised explanation and teach-back grading, and
every call has a deterministic fallback, so the demo runs with no network.

Content: 2 topics, 17 concepts, 45 items, 14 named misconceptions.
Tests: test_api.py (real routes), test_llm.py (5 LLM paths incl. failure),
simulate.py (learner and struggler student simulations)."

# 5. connect and push
git remote add origin https://github.com/only1abhimanyu/titan_protocol.git
git push -u origin main
```

If GitHub asks for a password, it wants a **Personal Access Token**, not your
account password: GitHub → Settings → Developer settings → Personal access
tokens → Tokens (classic) → Generate new token → tick `repo` → copy it and
paste it as the password. Or run `gh auth login` once if you have the GitHub CLI.

---

## Sharing it with the team

A private repo is invisible until you add people.

**Settings → Collaborators → Add people** → GitHub usernames or emails →
role **Write** for teammates who will push, **Read** for anyone who only needs
to see it. They get an email invite and must accept it.

On GitHub's free plan, private repos allow unlimited collaborators, so this
costs nothing. If Titan Protocol has a GitHub **organisation**, consider
creating the repo under the org instead of your personal account — it survives
you leaving the team and makes future handover trivial. You can transfer an
existing repo: Settings → General → Danger zone → Transfer ownership.

---

## What NOT to commit

`.gitignore` already covers these, but they are worth knowing:

- **`.env`** — a real `GEMINI_API_KEY` in git history is a leaked key. If it
  ever happens, rotate the key first, then worry about the history. `.env.example`
  is the committed template and contains no secrets.
- **`.venv/`** — hundreds of MB, and platform-specific. Teammates run
  `pip install -r requirements.txt`.
- **`sessions/`** — generated per-learner session JSON. Runtime data, not source.
- **`content/_calibration.json`** — only written when `CALIBRATE=1`. Generated.

---

## Verify the clone works

After pushing, confirm a teammate can actually run it — not just see files:

```bash
git clone https://github.com/only1abhimanyu/titan_protocol.git
cd titan_protocol
pip install -r requirements.txt
python app.py     # -> http://localhost:8000
```

If that works on a machine that has never seen this project, the repo is complete.

---

## If you'd rather not use a terminal

GitHub's web uploader works but is worse here: it ignores `.gitignore`
completely (so it will happily upload `__pycache__`), caps at 100 files per
drag, and gives you no commit history. Use it only as a last resort, and
upload from a copy of the folder that has never been run.

---

## Daily loop after the hackathon

```bash
git checkout -b feat/add-topic-os
# ...work...
git add -A && git commit -m "Add OS topic: processes and scheduling"
git push -u origin feat/add-topic-os
```

Then open a pull request on GitHub. With two or more people pushing, work on
branches rather than all committing to `main` — it costs one extra command and
saves the "who overwrote my file" conversation.
