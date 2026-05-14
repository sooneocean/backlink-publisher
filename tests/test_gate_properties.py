"""Property-based tests for gate primitives.

Defends against the "tautological gate" bug class documented in
`docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md`
— a gate that returns True for every input passes example-based tests
forever without anyone noticing. Hypothesis generates adversarial inputs
and asserts structural invariants: the gate MUST distinguish at least one
known-bad input from a known-good one.

Each test below pairs a positive assertion ("gate accepts X") with a
negative-shape assertion ("gate rejects Y") sourced from a fixture the
gate is contractually required to reject. If a future maintainer
accidentally re-introduces a tautological code path, hypothesis will
generate a counterexample and the negative-shape assertion fails loudly.

Gates currently under property-test coverage:
- ``verify_publish._title_in_body`` — title substring presence
- ``verify_publish._link_in_body`` — any-link substring presence
- ``anchor_metrics.normalize`` — text normalization for distribution math
- ``language_check.language_matches`` — language gate that returns False for
  known mismatches. This is the gate whose absence of property coverage let
  R1's always-True regression ship undetected through 999 example tests.

Gates intentionally out of scope here:
- ``linkcheck`` — HTTP-bound; the pure parts are too thin to property-test
  meaningfully without mocking the network.
"""

from __future__ import annotations

import random
import string

from hypothesis import assume, given
from hypothesis import strategies as st

from backlink_publisher.anchor_metrics import normalize
from backlink_publisher.language_check import (
    EN_HINTS,
    RU_HINTS,
    SUPPORTED_LANGUAGES,
    ZH_HINTS,
    detect_language,
    language_matches,
)
from backlink_publisher.verify_publish import (
    _link_in_body,
    _title_in_body,
)


# ── verify_publish._title_in_body ────────────────────────────────────────────


@given(
    title=st.text(min_size=1, max_size=80).filter(lambda s: s.strip() != ""),
)
def test_title_in_body_positive_when_title_appears_verbatim(title):
    """Property: if a title appears in the body (case-insensitive), the gate accepts."""
    # Body contains the title prefix (up to 40 chars, matching gate logic)
    body = f"prefix some content {title} suffix more content"
    assert _title_in_body(title, body) is True


@given(
    title=st.text(min_size=10, max_size=80).filter(
        lambda s: s.strip() != "" and any(c.isalnum() for c in s)
    ),
    junk=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=20,
        max_size=100,
    ),
)
def test_title_in_body_negative_when_unrelated(title, junk):
    """Property: body that has no overlap with the title is rejected.

    This is the load-bearing negative-shape assertion: if the gate were
    rewritten to return True unconditionally, this property would fail
    immediately on the first hypothesis-generated counterexample.
    """
    # Construct an unrelated body that definitely does NOT contain the title.
    # Take only ASCII letters from the title prefix to build the negative key.
    title_prefix = title[:40].strip().lower()
    assume(len(title_prefix) >= 5)  # need enough signal
    # Generate junk that does NOT contain any 5-char substring of the title.
    five_grams = {title_prefix[i:i+5] for i in range(len(title_prefix) - 4)}
    junk_lower = junk.lower()
    assume(not any(g in junk_lower for g in five_grams))
    assert _title_in_body(title, junk) is False


def test_title_in_body_empty_title_accepts():
    """Documented behavior: empty title is treated as 'no constraint' (accept)."""
    assert _title_in_body("", "any body content") is True
    assert _title_in_body("", "") is True


def test_title_in_body_known_negative_fixture():
    """Hard-coded negative: a published page that does NOT contain the title.

    This is the test that would catch the gate going tautological. If
    `_title_in_body` is ever changed to `return True` blindly, this test
    fails — no hypothesis run required.
    """
    title = "Best laptops 2026 — comprehensive buying guide"
    body = "<html><body>404 — Not Found</body></html>"
    assert _title_in_body(title, body) is False


# ── verify_publish._link_in_body ─────────────────────────────────────────────


@given(
    link=st.from_regex(r"https://[a-z]{3,15}\.com/[a-z]{3,15}", fullmatch=True),
)
def test_link_in_body_positive_when_link_appears(link):
    """Property: if any required link appears in the body, the gate accepts."""
    body = f"prefix <a href='{link}'>anchor</a> suffix"
    assert _link_in_body([link], body) is True


@given(
    link=st.from_regex(r"https://[a-z]{3,15}\.com/[a-z]{3,15}", fullmatch=True),
    body_text=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs")),
        min_size=10,
        max_size=100,
    ),
)
def test_link_in_body_negative_when_unrelated(link, body_text):
    """Property: body without the link rejects."""
    assume(link not in body_text)
    # Also assume no substring overlap that could spuriously match
    assume(link[:20] not in body_text)
    assert _link_in_body([link], body_text) is False


def test_link_in_body_empty_list_accepts():
    """Documented behavior: empty required-links list is 'no constraint'."""
    assert _link_in_body([], "any body") is True
    assert _link_in_body([], "") is True


def test_link_in_body_known_negative_fixture():
    """Hard-coded negative: published page that does NOT link to the target."""
    required = ["https://target-site.example/money-page"]
    body = "<html><body>Some unrelated content with no outbound links.</body></html>"
    assert _link_in_body(required, body) is False


def test_link_in_body_partial_match_accepts():
    """Documented behavior: substring match is enough (no exact URL parsing)."""
    required = ["https://example.com/page"]
    # Body contains the URL with a query string suffix — still matches
    body = '<a href="https://example.com/page?ref=campaign">link</a>'
    assert _link_in_body(required, body) is True


# ── anchor_metrics.normalize ─────────────────────────────────────────────────


@given(text=st.text(min_size=0, max_size=200))
def test_normalize_idempotent(text):
    """Property: normalize(normalize(x)) == normalize(x).

    Idempotency is a structural invariant of any text-normalization function.
    Violating it means the function has hidden state or non-deterministic
    behavior — both are bug-class signals.
    """
    once = normalize(text)
    twice = normalize(once)
    assert once == twice


@given(text=st.text(min_size=1, max_size=80))
def test_normalize_case_invariant(text):
    """Property: normalize(text.upper()) == normalize(text.lower())."""
    assert normalize(text.upper()) == normalize(text.lower())


@given(
    text=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
        min_size=1,
        max_size=50,
    ),
)
def test_normalize_preserves_alphanumerics(text):
    """Property: alphanumeric characters survive normalization (after casefold).

    The gate explicitly does NOT strip punctuation or fold diacritics —
    only collapses whitespace and casefolds. Alphanumerics MUST survive.
    """
    result = normalize(text)
    # Every alphanumeric char in input (after casefold) should appear in output
    for ch in text.casefold():
        if ch.isalnum():
            assert ch in result, f"alphanumeric {ch!r} dropped from normalize({text!r}) = {result!r}"


def test_normalize_brand_variants_remain_distinct():
    """Load-bearing negative-shape: 'Lyft, Inc.' and 'Lyft Inc' must NOT collapse.

    This is the property that the document-review flagged (F4 in PR #11
    rev-2 review). If a future maintainer adds punctuation-stripping to
    normalize, this test fails — preserving the false-positive defense.
    """
    assert normalize("Lyft, Inc.") != normalize("Lyft Inc.")
    assert normalize("O'Reilly") != normalize("OReilly")
    assert normalize("Yahoo!") != normalize("Yahoo")


def test_normalize_whitespace_collapses():
    """Documented behavior: internal whitespace runs collapse to one space."""
    assert normalize("a   b") == "a b"
    assert normalize("a\t\nb") == "a b"
    assert normalize("  leading and trailing  ") == "leading and trailing"


def test_normalize_empty_string_stays_empty():
    assert normalize("") == ""
    assert normalize("   ") == ""
    assert normalize("\t\n") == ""


# ── language_check.language_matches ──────────────────────────────────────────
#
# Backfill for the always-True regression captured at
# ``docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md``.
# The gate's contract (per R1 in plan 2026-05-14-001):
# - ``"unknown"`` or out-of-enum on either side → True (cannot disprove)
# - Two known, equal supported langs → True
# - Two known, different supported langs → False
#
# The structural-tautology guard at the end of this section is the property
# whose absence is exactly what let the buggy `return True` ship through 999
# example tests. If a future maintainer reintroduces a tautological branch,
# the guard fails on the first sampled mismatch.


@given(lang=st.sampled_from(sorted(SUPPORTED_LANGUAGES)))
def test_language_matches_positive_when_langs_equal(lang):
    """Property: equal supported langs always match."""
    assert language_matches(lang, lang) is True


@given(data=st.data())
def test_language_matches_negative_when_known_mismatch(data):
    """Property: any two distinct supported langs do NOT match.

    This is the load-bearing negative-shape assertion. If `language_matches`
    is rewritten to always return True (the historical regression), this
    property fails on the first hypothesis-generated counterexample.
    """
    supported = sorted(SUPPORTED_LANGUAGES)
    a = data.draw(st.sampled_from(supported))
    b = data.draw(st.sampled_from(supported))
    assume(a != b)
    assert language_matches(a, b) is False, (
        f"distinct supported langs {a!r} vs {b!r} unexpectedly matched"
    )


@given(known=st.sampled_from(sorted(SUPPORTED_LANGUAGES) + ["zh-Hant", "ja", "de", ""]))
def test_language_matches_unknown_escape_valve(known):
    """Property: ``"unknown"`` on either side returns True — the escape valve.

    Documented R1 contract: when the gate cannot tell what one side is,
    it can't disprove a mismatch, so it passes.
    """
    assert language_matches("unknown", known) is True
    assert language_matches(known, "unknown") is True


@given(
    supported=st.sampled_from(sorted(SUPPORTED_LANGUAGES)),
    out_of_enum=st.sampled_from(["zh-Hant", "ja", "de", "fr", "ko", "", "xx"]),
)
def test_language_matches_out_of_enum_treated_as_unknown(supported, out_of_enum):
    """Property: out-of-enum lang values are coerced to ``"unknown"`` semantics.

    Per R1 contract: if a side falls outside SUPPORTED_LANGUAGES, the gate
    cannot speak for it, so it passes. This is intentional — the gate is
    conservative for languages it has no hint set for.
    """
    assert language_matches(supported, out_of_enum) is True
    assert language_matches(out_of_enum, supported) is True


def test_language_matches_known_negative_fixture():
    """Hard-coded negative: the exact pair from the bug capture document.

    Pre-R1, ``language_matches("en", "zh-CN")`` returned True (every branch
    fell through to ``return True``). The fix in commit ``f08423b`` makes
    this return False. If this test ever passes True again, the structural
    bug class from the bug capture has been re-introduced.
    """
    assert language_matches("en", "zh-CN") is False
    assert language_matches("zh-CN", "en") is False
    assert language_matches("ru", "en") is False
    assert language_matches("en", "ru") is False


def test_language_matches_not_tautological():
    """Structural guard: over 10000 sampled inputs, both True and False fire.

    This is the property whose absence let R1's always-True regression ship
    through 999 example tests. A tautological gate ``return True`` violates
    the False-rate floor; a vacuous ``return False`` violates the True-rate
    floor. Either rewrites the function into structural nonsense and this
    test fires.

    Sampling space: supported langs + ``"unknown"`` + a synthetic out-of-enum
    value (mimics how the gate is called in practice — detect_language()
    can return either a supported lang or ``"unknown"``, and operator config
    occasionally lists langs we don't have hint sets for).

    Floor is 5% on each side; the true rates under the current contract are
    roughly 53% True / 47% False given the sampling distribution, so a 5%
    floor leaves wide margin for legitimate refactors while still catching
    structural collapse to a single return value.
    """
    rng = random.Random(0xBADC0DE)  # deterministic seed for CI stability
    sample_space = sorted(SUPPORTED_LANGUAGES) + ["unknown", "ja", ""]
    n = 10_000
    true_count = 0
    false_count = 0
    for _ in range(n):
        detected = rng.choice(sample_space)
        requested = rng.choice(sample_space)
        if language_matches(detected, requested):
            true_count += 1
        else:
            false_count += 1
    true_rate = true_count / n
    false_rate = false_count / n
    assert false_rate >= 0.05, (
        f"language_matches False-rate {false_rate:.3f} below floor 0.05 — "
        f"the gate looks tautologically True (regression of R1's bug class). "
        f"True={true_count}, False={false_count}"
    )
    assert true_rate >= 0.05, (
        f"language_matches True-rate {true_rate:.3f} below floor 0.05 — "
        f"the gate looks tautologically False (would break the unknown "
        f"escape valve and reject all rows). "
        f"True={true_count}, False={false_count}"
    )


def test_language_matches_via_detection_pipeline_negative():
    """End-to-end: detect_language on EN body + target zh-CN ⇒ gate rejects.

    Captures the operator-facing failure mode (an English article that
    claims to be zh-CN was the original incident). If either
    ``detect_language`` or ``language_matches`` regresses to a tautology,
    this end-to-end test catches it without needing the structural guard.
    """
    en_body = " ".join(EN_HINTS * 3)  # dense English signal
    detected = detect_language(en_body)
    assert detected == "en", f"expected EN detection, got {detected!r}"
    assert language_matches(detected, "zh-CN") is False


def test_language_matches_via_detection_pipeline_positive():
    """End-to-end happy path: detected lang matches requested lang."""
    zh_body = " ".join(ZH_HINTS * 3)
    detected = detect_language(zh_body)
    assert detected == "zh-CN"
    assert language_matches(detected, "zh-CN") is True

    ru_body = " ".join(RU_HINTS * 3)
    detected = detect_language(ru_body)
    assert detected == "ru"
    assert language_matches(detected, "ru") is True
