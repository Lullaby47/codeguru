"""
Unified OpenAI client.

All modules that need OpenAI should import from here:
    from app.ai.openai_client import get_client, key_present, key_fingerprint, last_error

This ensures:
- The API key is read ONCE and stripped of whitespace.
- A single client instance is reused.
- AuthenticationError is caught cleanly everywhere.
"""
import os

_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()

# Track last error for diagnostics
_last_error: str | None = None

try:
    import openai as _openai
    _LIB_OK = True
except ImportError:
    _openai = None  # type: ignore
    _LIB_OK = False

# Lazily-created singleton
_client = None


def key_present() -> bool:
    return bool(_KEY)


def key_fingerprint() -> str:
    """Return masked key for safe logging: sk-xxxx...1234"""
    if not _KEY:
        return "(not set)"
    if len(_KEY) <= 10:
        return _KEY[:2] + "***"
    return _KEY[:6] + "..." + _KEY[-4:]


def get_client():
    """
    Return the shared OpenAI client, or None if library/key missing.
    """
    global _client
    if not _LIB_OK:
        return None
    if not _KEY:
        return None
    if _client is None:
        _client = _openai.OpenAI(api_key=_KEY, timeout=12)
    return _client


def set_last_error(msg: str):
    global _last_error
    _last_error = msg


def get_last_error() -> str | None:
    return _last_error


def log_startup():
    """Print one-time startup diagnostics."""
    print(f"[AI] OPENAI_API_KEY present: {key_present()}", flush=True)
    print(f"[AI] key fingerprint: {key_fingerprint()}", flush=True)
    if _LIB_OK:
        print("[AI] openai library: installed", flush=True)
    else:
        print("[AI] openai library: NOT installed", flush=True)
