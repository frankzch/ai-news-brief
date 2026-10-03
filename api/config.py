"""API settings. Loopback-only by default; CORS limited to the Vite dev server."""
import os

HOST = "127.0.0.1"
PORT = 8000

DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def cors_origins() -> list[str]:
    raw = os.environ.get("API_CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    # Never allow a wildcard, even if configured.
    origins = [o for o in origins if o != "*"]
    return origins or list(DEFAULT_CORS_ORIGINS)
