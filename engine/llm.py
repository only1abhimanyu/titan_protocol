"""LLM adapter.

Hard rule for this build: **the demo must never depend on the network.** Every
LLM call takes a deterministic fallback callable. If there is no key, if the API
is slow, if it rate-limits, or if it returns malformed JSON, the fallback runs
and the session continues seamlessly. `source` on every response says which path
was taken.

Provider priority:
  1. an explicit per-user key (from the encrypted vault)
  2. GROQ_API_KEY            <- the project default
  3. GEMINI_API_KEY / OPENAI_API_KEY
  4. offline (fallbacks only)
"""
from __future__ import annotations

import hashlib
import json
import os
import re

GROQ_BASE = "https://api.groq.com/openai/v1"


class LLM:
    # class-level so per-user instances share the cache
    _cache: dict[str, dict] = {}

    def __init__(self, api_key: str | None = None, provider: str | None = None):
        self.user_key = (api_key or "").strip() or None
        self.user_provider = provider
        self.groq_key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY")
        self.gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.openai_key = os.environ.get("OPENAI_API_KEY")
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.groq_model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.gemini_model = os.environ.get("LLM_MODEL", "gemini-2.0-flash")
        self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout = float(os.environ.get("LLM_TIMEOUT", "25"))
        self.stats = {"llm": 0, "cache": 0, "fallback": 0}
        self.last_error = None

    # ------------------------------------------------------------------ state
    @property
    def provider(self) -> str:
        if self.user_key:
            return self.user_provider or "user"
        if self.groq_key:
            return "groq"
        if self.gemini_key:
            return "gemini"
        if self.openai_key:
            return "openai"
        return "offline"

    @property
    def model(self) -> str | None:
        p = self.provider
        if p == "groq":
            return self.groq_model
        if p == "gemini":
            return self.gemini_model
        if p in ("openai", "user"):
            return self.openai_model if p == "openai" else self.groq_model
        return None

    @property
    def available(self) -> bool:
        return self.provider != "offline"

    # ------------------------------------------------------------------- call
    def json_call(self, system: str, user: str, fallback, tag: str = "") -> dict:
        # a user-supplied key must not read another user's cached generation
        scope = "user" if self.user_key else "default"
        key = hashlib.sha256(f"{scope}|{tag}|{system}|{user}".encode()).hexdigest()
        if key in self._cache:
            self.stats["cache"] += 1
            out = dict(self._cache[key])
            out["source"] = out.get("source", "llm") + "+cache"
            return out
        if not self.available:
            self.stats["fallback"] += 1
            out = fallback()
            out["source"] = "template"
            return out

        try:
            text = self._raw(system, user)
            data = self._parse_json(text)
            if data is None:
                raise ValueError("unparseable JSON from model")
            data["source"] = "llm"
            self._cache[key] = data
            self.stats["llm"] += 1
            return data
        except Exception as e:                                  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            self.stats["fallback"] += 1
            out = fallback()
            out["source"] = "template"
            return out

    def _raw(self, system: str, user: str) -> str:
        import httpx

        p = self.provider

        if p in ("groq", "user"):
            key = self.user_key if p == "user" else self.groq_key
            base = GROQ_BASE if p == "groq" or (self.user_provider or "groq") == "groq" \
                else self.base_url
            body = {
                "model": self.groq_model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": 0.4,
                "response_format": {"type": "json_object"},
            }
            r = httpx.post(f"{base}/chat/completions", json=body, timeout=self.timeout,
                           headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

        if p == "gemini":
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{self.gemini_model}:generateContent")
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json",
                                     "temperature": 0.45, "maxOutputTokens": 4096},
            }
            r = httpx.post(url, params={"key": self.gemini_key}, json=body,
                           timeout=self.timeout)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]

        body = {
            "model": self.openai_model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }
        r = httpx.post(f"{self.base_url}/chat/completions", json=body,
                       timeout=self.timeout,
                       headers={"Authorization": f"Bearer {self.openai_key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_json(text: str):
        if not text:
            return None
        text = text.strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return None
        return None
