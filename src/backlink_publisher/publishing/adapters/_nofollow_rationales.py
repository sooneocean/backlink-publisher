"""Per-platform nofollow rationale strings for register() calls.

Extracted from adapters/__init__.py to keep the dispatch table readable.
Each key is the platform slug; value is the ≥80-char rationale required
by the monolith_budget.toml discipline when ``dofollow=False``.

Maintainer note: update this file when adding a new nofollow platform,
not adapters/__init__.py.
"""

from __future__ import annotations

NOFOLLOW_RATIONALES: dict[str, str] = {
    "hatena": (
        "Hatena Blog post bodies render outbound <a> with no rel by default — a "
        "2026-05-29 3rd-party live probe (scripts/channel_probe.py) sampled 11/12 "
        "body links dofollow, 1 nofollow, with no redirect interstitial. One of "
        "Japan's largest blog hosts (high DA) and the only one of the three GO "
        "candidates with a documented AtomPub publish API. Registered "
        'dofollow="uncertain" pending an OUR-pipeline canary confirming our own '
        "placed link renders dofollow; operator flips to True via "
        "verify_link_attributes (livejournal/txtfyi workflow). referral_value="
        "high regardless: JP DA + referral traffic + indexation speed."
    ),
    "devto": (
        "Dev.to applies rel=\"nofollow ugc\" to outbound links since "
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
        "Mastodon hardcodes rel=\"nofollow noopener noreferrer\" on "
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
        "LinkedIn applies rel=\"nofollow ugc\" to outbound links in user posts "
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
        "Registered dofollow=\"uncertain\" pending the R4 canary loop "
        "(Plan 2026-05-25-001 Unit 7): Phase 0 probe confirmed txt.fyi serves "
        "raw static HTML with no server-side link rewriting, so outbound <a> "
        "elements are expected to carry no rel=\"nofollow\" server-side, but "
        "the definitive status is confirmed only by publishing a canary and "
        "reading verify_link_attributes on the live page, then amending this "
        "register() to dofollow=True. referral_value=\"low\" reflects "
        "txt.fyi's anonymous-pastebin character: the site has modest DA and "
        "is not indexed aggressively (robots.txt disallow), but links on "
        "dofollow static pages still pass equity to any crawler that reaches "
        "them. No credentials needed; the form-POST adapter composes the "
        "Unit 4 http_form_post helpers for a zero-dependency publish path."
    ),
    # --- Phase 1 channel-expansion dofollow truth audit (2026-05-26) ---
    # The 16 Phase-1 adapters shipped with bare ``dofollow=True`` and no
    # evidence. This audit downgrades every one: hard server-side
    # nofollow/redirect-interstitial evidence => dofollow=False; anything
    # lacking an OUR-pipeline canary => dofollow="uncertain" (the
    # livejournal/txtfyi precedent — third-party spot-checks do not
    # discharge the canary burden). Zero stay dofollow=True.
    "wordpresscom": (
        "Registered dofollow=\"uncertain\": evidence conflicts. This "
        "project's PR #108->#109 (the 9-minute revert) observed that free "
        "*.wordpress.com tier adds rel=nofollow to outbound links, but a "
        "2026-05 re-check found nofollow applied only opt-in per-link "
        "(\"Mark as nofollow\" checkbox), not automatically — WordPress.com "
        "may have changed policy. The definitive status is resolved only by "
        "publishing a canary on a free-tier blog and reading "
        "verify_link_attributes, then amending this register(). "
        "referral_value=\"high\" reflects wordpress.com's DA ~94 and strong "
        "referral reach regardless of the rel outcome."
    ),
    "substack": (
        "Registered dofollow=\"uncertain\" pending an OUR-pipeline canary. A "
        "2026-05 third-party live check of a published Substack post found "
        "external body <a> carry no rel attribute (= dofollow), but a "
        "third-party spot-check does not discharge the canary burden "
        "(livejournal/txtfyi precedent). Confirm by publishing a canary and "
        "reading verify_link_attributes on the live post, then amend to "
        "dofollow=True. referral_value=\"high\": high-DA newsletter platform "
        "with strong referral reach."
    ),
    "hashnode": (
        "Registered dofollow=\"uncertain\" pending an OUR-pipeline canary. A "
        "2026-05 third-party live check found Hashnode post-body external <a> "
        "carry no rel attribute (= dofollow), but a third-party spot-check "
        "does not discharge the canary burden (livejournal/txtfyi "
        "precedent). NOTE: Hashnode is concurrently slated for retirement "
        "(PR #204) and its GraphQL publish path hits a paywall — coordinate "
        "before investing further. referral_value=\"high\": high-DA dev "
        "blogging platform."
    ),
    "writeas": (
        "Registered dofollow=\"uncertain\" pending an OUR-pipeline canary. A "
        "2026-05 third-party live check found write.as post-body external <a> "
        "(including embeds) carry no rel attribute (= dofollow), but a "
        "third-party spot-check does not discharge the canary burden "
        "(livejournal/txtfyi precedent). NOTE: write.as is concurrently "
        "slated for retirement (PR #202) — coordinate before investing "
        "further. referral_value=\"low\": minimalist low-DA blogging host."
    ),
    "rentry": (
        "Registered dofollow=\"uncertain\" pending an OUR-pipeline canary. A "
        "2026-05 third-party live check found rentry.co paste links carry "
        "only rel=\"noreferrer noopener\" with no nofollow (= dofollow), but "
        "a third-party spot-check does not discharge the canary burden "
        "(livejournal/txtfyi precedent). Confirm by publishing a canary and "
        "reading verify_link_attributes, then amend to dofollow=True. "
        "referral_value=\"low\": anonymous markdown paste with low DA and "
        "frequent noindex, so equity is weak even if dofollow holds."
    ),
    "livejournal": (
        "Pipeline canary 2026-05-29: link_attr_verification target_nofollow=True — "
        "LiveJournal platform-wide injects rel=nofollow on external body links. "
        "Registered dofollow=False. Kept as referral channel: referral_value=\"high\" — "
        "high-DA legacy blogging platform; nofollow does not eliminate referral or brand value."
    ),
    "hackmd": (
        "Registered dofollow=\"uncertain\" pending an OUR-pipeline canary. A "
        "2026-06-01 third-party live check (verify_link_attributes on a real public "
        "note) sampled 188 outbound anchors with 0 nofollow and <meta robots=\"index,"
        "follow\"> (DA ~71). A third-party spot-check does not discharge the canary "
        "burden (hashnode/substack/hatena precedent): publish an OUR note, read "
        "verify_link_attributes, then amend to dofollow=True. referral_value=\"high\": "
        "well-indexed high-DA docs host with real referral traffic."
    ),
    "mataroa": (
        "Registered dofollow=\"uncertain\" pending an OUR-pipeline canary. A "
        "2026-06-01 third-party live check (verify_link_attributes on real posts) "
        "found outbound external links carry no rel (= dofollow) and site:mataroa.blog "
        "returns fresh indexed content. The platform currently tolerates marketing "
        "posts, so it could tighten — confirm via an OUR canary and read "
        "verify_link_attributes before amending to dofollow=True. referral_value="
        "\"high\": indexed minimalist blog host with open token API."
    ),
    "gitlabpages": (
        "Registered dofollow=\"uncertain\" though the rel is operator-controlled "
        "(GitLab Pages serves our own static HTML verbatim, no nofollow injection). "
        "The uncertainty is indexation, not rel: *.gitlab.io indexation is only "
        "\"partial\" per the 2026-06-01 discovery run, the publish is async (CI "
        "pages pipeline), and a shared free subdomain carries search-trust risk. "
        "An OUR-post canary confirming the served page is index,follow gates the "
        "flip to dofollow=True. referral_value=\"high\": high-DA operator-owned host."
    ),
}
