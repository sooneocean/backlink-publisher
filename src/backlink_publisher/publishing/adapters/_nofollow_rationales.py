"""Per-platform nofollow rationale strings for register() calls.

Extracted from adapters/__init__.py to keep the dispatch table readable.
Each key is the platform slug; value is the ≥80-char rationale required
by the monolith_budget.toml discipline when ``dofollow=False``.

Maintainer note: update this file when adding a new nofollow platform,
not adapters/__init__.py.
"""

from __future__ import annotations

NOFOLLOW_RATIONALES: dict[str, str] = {
    "devto": (
        'Dev.to applies rel="nofollow ugc" to outbound links since '
        "~2022 per platform policy; every external <a> is decorated "
        "server-side regardless of account tier or post format. "
        "DevtoAPIAdapter (Plan 2026-05-21-003 Phase 2 Unit 7) is the "
        "preferred path for operators with an API key; "
        "BrowserPublishDispatcher is the fallback for operators without "
        "one (DependencyError → fall through per registry contract). "
        "backlinks here still drive referral traffic and topical "
        "relevance signals even though they don't transfer PageRank."
    ),
    "notion": (
        "Notion applies rel=nofollow to outbound hyperlinks on public "
        "pages — all <a> elements in Notion-rendered content carry "
        "nofollow regardless of account type or database visibility. "
        "This adapter's value is entity signal (DA ~75+), content "
        "syndication speed, and indexation acceleration. "
        "Plan 2026-05-21-003 Phase 2 Unit 6."
    ),
    "mastodon": (
        'Mastodon hardcodes rel="nofollow noopener noreferrer" on '
        "outbound links across all instances — federation-default and "
        "not disableable per-post or per-account. Re-registered in "
        "Plan 2026-05-21-001 Unit 4c as a chrome publish channel — "
        "Fediverse referral traffic + topical signal value despite the "
        "nofollow. Single instance per config.toml [mastodon] "
        "instance_url; security policy: use a throwaway account only, "
        "never a personal Mastodon identity."
    ),
    "tumblr": (
        "Tumblr rewrites all outbound <a> href via t.umblr.com/redirect "
        "which strips link equity — server-side and compulsory for all "
        "accounts. The adapter is retained for referral traffic and "
        "topical signal from a platform with high DA and strong "
        "content-discovery reach. OAuth 1.0a credentials (consumer_key, "
        "consumer_secret, oauth_token, oauth_token_secret) plus blog_name "
        "are stored in a 0600 JSON file. Post body is HTML rendered from "
        "content_markdown. Tags are comma-separated, capped at 20."
    ),
    "linkedin": (
        'LinkedIn applies rel="nofollow ugc" to outbound links in user posts '
        "and articles server-side irrespective of account type — verified across "
        "multiple accounts and post formats. The platform is retained for brand "
        "exposure, B2B referral traffic, and topical relevance signalling rather "
        "than direct PageRank transfer. DA ~95. LinkedIn's w_member_social OAuth "
        "scope requires LinkedIn-app verification (operator responsibility; the "
        "adapter raises DependencyError when the token file is absent or has "
        "insufficient scope). Post body is HTML; max commentary length is 3000 "
        "chars enforced server-side."
    ),
    "txtfyi": (
        "Playwright 2026-06-06 confirmed txt.fyi is a plain-text pastebin "
        "that does NOT render Markdown link syntax [text](url) as clickable "
        "<a> HTML elements. Backlinks placed in the page body appear only "
        "as raw URL text — no PageRank transfer through plain text. "
        "Adapter retained for AI citation discovery (crawlers can still "
        "read and cite text URLs from the indexed page) but no dofollow "
        "backlink value. referral_value=\"low\"; visibility=\"hidden\" "
        "prevents operator confusion."
    ),
    # --- Phase 1 channel-expansion dofollow truth audit (2026-05-26) ---
    # The 16 Phase-1 adapters shipped with bare ``dofollow=True`` and no
    # evidence. This audit downgrades every one: hard server-side
    # nofollow/redirect-interstitial evidence => dofollow=False; anything
    # lacking an OUR-pipeline canary => dofollow="uncertain" (the
    # livejournal/txtfyi precedent — third-party spot-checks do not
    # discharge the canary burden). Zero stay dofollow=True.
    "wordpresscom": (
        'Registered dofollow="uncertain": evidence conflicts. This '
        "project's PR #108->#109 (the 9-minute revert) observed that free "
        "*.wordpress.com tier adds rel=nofollow to outbound links, but a "
        "2026-05 re-check found nofollow applied only opt-in per-link "
        '("Mark as nofollow" checkbox), not automatically — WordPress.com '
        "may have changed policy. The definitive status is resolved only by "
        "publishing a canary on a free-tier blog and reading "
        "verify_link_attributes, then amending this register(). "
        'referral_value="high" reflects wordpress.com\'s DA ~94 and strong '
        "referral reach regardless of the rel outcome."
    ),
    "hashnode": (
        "Playwright 2026-06-06 confirmed article-body external links carry "
        'rel="noopener noreferrer nofollow ugc" server-side (observed on '
        "davidyau1.hashnode.dev post with a MySQL documentation link). "
        "Hashnode applies nofollow to all user-embedded external links "
        "regardless of account tier. Adapter retained for referral traffic "
        'and topical signals from a high-DA dev platform (DA ~90+).'
    ),
    "livejournal": (
        "Pipeline canary 2026-05-29: link_attr_verification target_nofollow=True — "
        "LiveJournal platform-wide injects rel=nofollow on external body links. "
        'Registered dofollow=False. Kept as referral channel: referral_value="high" — '
        "high-DA legacy blogging platform; nofollow does not eliminate referral or brand value."
    ),
    "hackmd": (
        "Playwright 2026-06-06 confirmed user note external links carry "
        'rel="noopener ugc nofollow" server-side (observed on a HackMD '
        "markdown cheatsheet note with rendered Markdown links). HackMD "
        "applies nofollow to all user-embedded external links regardless "
        "of note visibility or account tier. Adapter retained for referral "
        'traffic and topical signals from a well-indexed docs host (DA ~71).'
    ),
    "qiita": (
        "Qiita applies rel=nofollow noopener to every outbound external link "
        "server-side — confirmed on 12 real Qiita articles in the 2026-06-01 "
        "discovery run (12/86 non-nofollow links, all internal to Qiita). "
        "Zero PageRank transfer. Value = entity signal + JP referral traffic "
        'on a top JP dev platform (DA ~90+). referral_value="high".'
    ),
    "zenn": (
        "Zenn applies rel=nofollow noopener noreferrer to every outbound "
        "external link server-side — confirmed on 36 real Zenn articles in "
        "the 2026-06-01 discovery run (36/137 non-nofollow links, all "
        "Zenn-internal). Zero PageRank transfer. Value = entity signal + "
        'JP referral traffic on a top JP dev platform (DA ~90+). referral_value="high".'
    ),
}
