"""Comment-region detection over server-rendered HTML.

Maps a fetched page to a tri-state ``comment_open``:

- ``True``  — a comment/reply region marker is present in the server-side markup
- ``False`` — the page was fetched but carries no recognizable comment region
- ``None``  — the page was not fetchable (``fetch.fetch_comment_page`` returned a
  non-``ok`` reason); the caller passes ``html=None`` and detection abstains

These are **heuristics over static HTML**, deliberately conservative on precision: a
loose ``"comment"`` substring match would fire on HTML comments (``<!-- -->``), CSS
class soup, and analytics, starving ``qualify`` of trustworthy signal in the other
direction. Each signature targets a concrete platform marker or an explicit
comment/reply ``<form>``/``<textarea>``.

**Known limitation (documented, not a bug):** a page that mounts its comment widget
purely client-side with *no* server-side marker (a SPA discussion bundle) is detected as
``False``. We never execute JavaScript. The tri-state + conservative qualify ladder mean
such false-negatives drop the target rather than mis-accept it. Disqus/Commento/etc. are
still detected because their *mount point* (``id="disqus_thread"``, the embed URL) is in
the static HTML even when the thread itself loads later.
"""

from __future__ import annotations

import re
from typing import Optional

#: Each pattern is a specific comment/reply-region marker. Ordered loosely by platform;
#: any single match is sufficient. Byte-regex (IGNORECASE) to run on the raw body without
#: a decode step, mirroring ``_preflight_fetch``'s byte-regex discipline.
_SIGNATURES: tuple[re.Pattern[bytes], ...] = (
    # --- Disqus / hosted comment embeds -----------------------------------
    re.compile(rb"""id\s*=\s*["']disqus_thread["']""", re.IGNORECASE),
    re.compile(rb"disqus\.com/embed", re.IGNORECASE),
    re.compile(rb"\b(commento|utterances|giscus|hyvor|fastcomments|graphcomment)\b", re.IGNORECASE),
    # --- WordPress native -------------------------------------------------
    re.compile(rb"""id\s*=\s*["']respond["']""", re.IGNORECASE),
    re.compile(rb"""id\s*=\s*["']commentform["']""", re.IGNORECASE),
    re.compile(rb"wp-comments-post\.php", re.IGNORECASE),
    re.compile(rb"""class\s*=\s*["'][^"']*comment-(respond|form)""", re.IGNORECASE),
    # --- Generic comment <form> / <textarea> ------------------------------
    re.compile(rb"""<textarea[^>]+name\s*=\s*["']comment(\[[^"']*\])?["']""", re.IGNORECASE),
    re.compile(rb"""<form[^>]+action\s*=\s*["'][^"']*comment""", re.IGNORECASE),
    # --- Forum reply forms ------------------------------------------------
    re.compile(rb"""id\s*=\s*["'](quickreply|quick_reply|post_reply_form|newreply)["']""", re.IGNORECASE),
    re.compile(rb"""name\s*=\s*["'](post_reply|newreply)["']""", re.IGNORECASE),
    re.compile(rb"""class\s*=\s*["'][^"']*quick-reply""", re.IGNORECASE),
    # --- Explicit call-to-action text (anchored to avoid substring noise) --
    re.compile(rb"\b(leave|post|add|write) a (reply|comment)\b", re.IGNORECASE),
)


def detect_comment_region(html: Optional[bytes]) -> Optional[bool]:
    """Return tri-state ``comment_open`` for a fetched page body.

    ``None`` when ``html`` is ``None`` (page not fetchable). Otherwise ``True`` if any
    comment-region signature matches the server-side markup, else ``False``.
    """
    if html is None:
        return None
    return any(sig.search(html) for sig in _SIGNATURES)
