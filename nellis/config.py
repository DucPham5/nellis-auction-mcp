"""Central configuration — the one place values come from the environment.

Best-practice split:
- **Secrets** (the session cookie) have NO default. They must come from a local
  ``.env`` (git-ignored) and are ``None`` until you set them.
- **Public, non-sensitive values** (the Algolia search credentials, which Nellis
  ships to every browser) have sensible defaults baked in so the tool works out
  of the box — but stay overridable via env in case Nellis rotates them.

`.env` is loaded here once; other modules import from this file rather than
reading ``os.environ`` themselves.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # read a local .env into the environment (no-op if absent)


# --------------------------------------------------------------------------- #
# Public Algolia search API — browser-exposed, search-only. NOT secret.
# Defaults let the tool run with no .env; override via env only if they rotate.
# --------------------------------------------------------------------------- #
ALGOLIA_APP_ID: str = os.environ.get("NELLIS_ALGOLIA_APP_ID", "GL1QVP8R29")
ALGOLIA_API_KEY: str = os.environ.get(
    "NELLIS_ALGOLIA_API_KEY", "d22f83c614aa8eda28fa9eadda0d07b9"
)
ALGOLIA_INDEX: str = os.environ.get("NELLIS_ALGOLIA_INDEX", "nellisauction-prd")


# --------------------------------------------------------------------------- #
# Secret — the user's Nellis login session. No default; None until set in .env.
# --------------------------------------------------------------------------- #
_SESSION_COOKIE_VAR = "NELLIS_SESSION_COOKIE"


def session_cookie() -> str | None:
    """The Nellis session cookie from the environment, or None if unset.

    Read live (not cached at import) so a freshly-updated .env / env var takes
    effect without restarting.
    """
    return os.environ.get(_SESSION_COOKIE_VAR, "").strip() or None
