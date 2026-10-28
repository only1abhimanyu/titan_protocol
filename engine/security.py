"""Security: password hashing, session tokens, and the API-key vault.

Honest scope note (this matters, do not overclaim it):

- Passwords use scrypt with a per-user random salt. That is correct.
- Session tokens are HMAC-signed with a server secret, not JWT libraries.
- Stored third-party API keys are encrypted with Fernet (AES-128-CBC + HMAC)
  under a key derived from APP_SECRET.

What this is NOT: production secrets management. There is no KMS, no key
rotation, no per-user key derivation, and no audit log of key reads. If
APP_SECRET leaks, stored keys are recoverable. Set APP_SECRET in the
environment for anything beyond a demo; the file fallback is a convenience so
the demo survives a restart, not a security posture.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")

COOKIE = "ml_session"
TOKEN_TTL = 7 * 24 * 3600
SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)


# ------------------------------------------------------------------- secret
def _secret() -> bytes:
    env = os.environ.get("APP_SECRET")
    if env:
        return env.encode()
    path = os.path.join(DATA, ".app_secret")
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    val = secrets.token_bytes(32)
    with open(path, "wb") as f:
        f.write(val)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return val


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return dk.hex(), salt.hex()


def verify_password(password: str, pw_hash: str, pw_salt: str) -> bool:
    try:
        salt = bytes.fromhex(pw_salt)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    except Exception:
        return False
    return hmac.compare_digest(dk.hex(), pw_hash)


# ------------------------------------------------------------------- tokens
def issue_token(uid: str, ttl: int = TOKEN_TTL) -> str:
    exp = int(time.time()) + ttl
    body = f"{uid}.{exp}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{body}.{sig}".encode()).decode()


def read_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        uid, exp, sig = raw.rsplit(".", 2)
        body = f"{uid}.{exp}"
        want = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            return None
        if int(exp) < time.time():
            return None
        return uid
    except Exception:
        return None


# -------------------------------------------------------------------- vault
def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret()).digest())
    return Fernet(key)


def encrypt_key(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_key(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return None


def mask_key(key: str) -> str:
    """Safe to render in the UI: never returns enough to use the key."""
    k = (key or "").strip()
    if len(k) <= 8:
        return "•" * len(k)
    return f"{k[:4]}{'•' * 8}{k[-4:]}"


# ------------------------------------------------------------------ provider
PROVIDERS = {
    "groq": {"prefix": "gsk_", "label": "Groq", "help": "console.groq.com/keys"},
    "gemini": {"prefix": "AIza", "label": "Google Gemini", "help": "aistudio.google.com/apikey"},
    "openai": {"prefix": "sk-", "label": "OpenAI", "help": "platform.openai.com/api-keys"},
}


def detect_provider(key: str) -> str | None:
    k = (key or "").strip()
    for name, meta in PROVIDERS.items():
        if k.startswith(meta["prefix"]):
            return name
    return None


def validate_key(key: str) -> tuple[bool, str, str | None]:
    """(ok, message, provider). Shape check only - never logs the key."""
    k = (key or "").strip()
    if not k:
        return False, "Paste a key first.", None
    if len(k) < 20:
        return False, "That looks too short to be an API key.", None
    if any(c.isspace() for c in k):
        return False, "Keys do not contain spaces - check the paste.", None
    prov = detect_provider(k)
    if not prov:
        return False, ("Unrecognised key format. Groq keys start with gsk_, "
                       "Gemini with AIza, OpenAI with sk-."), None
    return True, "ok", prov
