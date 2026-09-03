"""Decode Remix's `turbo-stream` payload embedded in product page HTML.

Nellis switched product pages from plain flat JSON (`"currentPrice":41`) to this
format sometime between 2026-08-07 (last verified working) and 2026-08-28
(found broken). It's Remix's own serialization for loader data, needed because
plain JSON can't represent Dates/Maps/shared references — visible in the page
as `window.__remixContext.streamController.enqueue("[...]")`.

**The encoding, reverse-engineered from live payloads (see PROGRESS.md):**
Every value in the payload — no matter how deeply "nested" it conceptually is —
gets its own slot in ONE flat top-level JSON array, in serialization order.
A plain JS object doesn't appear as `{"realKey": value}`; it appears as a
*reference map* `{"_K": V, ...}` where `K` and `V` are themselves indices into
that same flat array: `K` points to the slot holding the real key's name
(a string), `V` points to the slot holding the real value (recursively
resolved the same way). This is why a naive regex that assumes a key's name
and value sit next to each other in the raw text is unreliable — adjacency is
just an accident of serialization order, not something the format promises.

Only non-negative ints are slot references. A negative int is a sentinel for a
type JSON can't hold directly (e.g. `undefined`) — none of our target fields
(price/bid/status numbers and strings) ever legitimately need one, so we treat
a negative sentinel as unresolvable and pass it through rather than guessing
at turbo-stream's exact tag scheme.

Validated against a real closed lot: decoded `currentPrice`/`retailPrice`
matched that lot's own `<meta description>` ("Sold for $2 | Retail: $726.24")
exactly, and the decoded `id` matched the requested product id.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import NellisParseError

_ENQUEUE_START_RE = re.compile(r'streamController\.enqueue\("')
_REF_KEY_RE = re.compile(r"_\d+")


def _js_string_body(html: str, start: int) -> str:
    """Return the contents of the JS string literal beginning at ``start``.

    ``start`` is the index just past the opening quote. Scans to the first
    unescaped closing quote, skipping two characters at each backslash.

    This is deliberately a scanner rather than a regex. The obvious pattern —
    `enqueue\\("(\\[.*?)"\\)` — is non-greedy and therefore stops at the FIRST
    `")` in the payload. Product descriptions routinely contain a parenthetical
    ending in an inch mark, e.g. `(width 22"-36", min height 13.75")`, which
    embeds that exact sequence and truncates the payload mid-string. The failure
    is silent (it surfaces later as "Unterminated string") and, worse, it is
    CORRELATED: items described by their dimensions are furniture, appliances
    and A/C units — the expensive end of the inventory. Measured at ~2.7% of
    lots before this fix.
    """
    i = start
    n = len(html)
    while i < n:
        c = html[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return html[start:i]
        i += 1
    raise NellisParseError(
        "turbo-stream payload had no closing quote — page truncated or structure changed"
    )


def decode(html: str) -> Any:
    """Extract and fully resolve the turbo-stream envelope from a product page.

    Returns the resolved envelope (a dict with `loaderData` / `actionData` /
    `errors` keys). Raises NellisParseError if the payload isn't found or
    isn't valid JSON — both mean the page structure changed again.
    """
    match = _ENQUEUE_START_RE.search(html)
    if not match:
        raise NellisParseError("No turbo-stream payload (streamController.enqueue) found on the page")

    body = _js_string_body(html, match.end())
    try:
        # `body` is a JS string literal's contents (escaped) -> unescape via JSON
        # string parsing, then parse the resulting array text.
        raw = json.loads('"' + body + '"')
        slots = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise NellisParseError(f"Could not parse the turbo-stream payload as JSON: {exc}") from exc

    if not isinstance(slots, list) or not slots:
        raise NellisParseError("turbo-stream payload was not the expected non-empty array")

    return _resolve(slots, 0, set())


def _resolve(slots: list, i: int, seen: frozenset) -> Any:
    """Resolve slot i, expanding a reference-map dict or an array of references."""
    if not (0 <= i < len(slots)):
        return None
    value = slots[i]

    is_ref_map = isinstance(value, dict) and value and all(_REF_KEY_RE.fullmatch(k) for k in value)
    if not is_ref_map and not isinstance(value, list):
        return value

    if i in seen:
        return None  # circular reference guard
    seen = seen | {i}

    if isinstance(value, list):
        # arrays are also reference-based: each element is a slot index (or a
        # negative sentinel) pointing at that element's real value, same as an
        # object's values are — e.g. bidHistory is an array of per-bid objects.
        return [_resolve_scalar_or_ref(slots, elem, seen) for elem in value]

    resolved: dict[str, Any] = {}
    for key_slot, value_slot in value.items():
        key_name = _resolve(slots, int(key_slot[1:]), seen)
        if not isinstance(key_name, str):
            continue  # malformed — skip rather than produce a garbage key
        resolved[key_name] = _resolve_scalar_or_ref(slots, value_slot, seen)
    return resolved


def _resolve_scalar_or_ref(slots: list, v: Any, seen: frozenset) -> Any:
    if isinstance(v, int) and not isinstance(v, bool):
        if v < 0:
            return None  # unresolved sentinel type (undefined, etc.) — see module docstring
        return _resolve(slots, v, seen)
    return v


def find_product(envelope: dict) -> dict:
    """Find the `product` object inside a decoded envelope's loaderData.

    Route ids are file-path-derived (e.g. `routes/p.$title.$productId._index`)
    and could shift if Nellis renames the route file, so we search by shape
    (whichever route's data has a `product` key) instead of hardcoding the id.
    """
    loader_data = envelope.get("loaderData")
    if not isinstance(loader_data, dict):
        raise NellisParseError("turbo-stream envelope had no loaderData")

    for route_data in loader_data.values():
        if isinstance(route_data, dict) and isinstance(route_data.get("product"), dict):
            return route_data["product"]

    raise NellisParseError("No route in loaderData carried a 'product' object — page structure changed")
