"""One deterministic word count, shared by every evidence field in both arms.

Measurement only since 2026-11-08 (:data:`WORD_BUDGET_VERSION` ``..._v2_...``). No
caller refuses a binding, a proposal or a task because of these numbers any more:
evidence prose is now an optional short sentence that is moved out of the engineering
JSON into a separate audit log (``evidence_log``). The constants and functions below
stay so that historical refusals can still be classified and old reports recomputed;
:data:`LIMIT_ENFORCED` states the current policy. The history below describes the
earlier, enforcing revision and is kept for that reason.

Why words and not characters: the published bound exists so that an evidence field
states the source facts a decision rests on instead of holding a reasoning transcript.
Characters measured that badly across languages - the same statement costs several
times more characters in one language than another - so the contract is now a word
count, declared in the request contract and checked locally with the same function.

Why not model tokens: a BPE count is model- and tokeniser-specific and cannot be
recomputed offline from the saved text alone. This count can.

The migration is explicit, not a silent tightening and not a silent relaxation: the
old character bounds are gone from the evidence fields rather than relabelled as word
counts, and a text that passed or failed the old character bound is never described as
having passed or failed this one. A raw response is never truncated, never re-asked and
never repaired to fit; an over-budget field fails its own check and the failure is
delivered as engineering JSON with the measurement in it.

Counting rule, in full:

* A run of alphanumeric characters is one word. An apostrophe or hyphen between two
  alphanumeric characters stays inside the run, so ``state-of-the-art`` is one word.
* Every CJK, kana or Hangul character counts as one word on its own, because such text
  carries no spaces and a space-delimited count would let an arbitrarily long passage
  through.
* Everything else - spaces, punctuation, symbols, line breaks - separates words and is
  not counted.

A separate, much larger byte ceiling exists only to refuse pathological input such as
one multi-megabyte "word". It is reported separately and is never called a word count.
"""

WORD_BUDGET_VERSION = "evidence_word_measure_v2_20261108"
#: The previous, enforcing revision, kept so historical records can be told apart.
PREVIOUS_WORD_BUDGET_VERSION = "evidence_word_budget_v1_20261107"
#: No caller refuses anything because of the numbers below. Measurement only.
LIMIT_ENFORCED = False
#: Historical bound of the v1 revision. Reported as a measurement reference only.
EVIDENCE_MAX_WORDS = 1200
#: Historical byte ceiling of the v1 revision. Reported as a measurement reference only.
EVIDENCE_MAX_BYTES = 200000
TOO_MANY_WORDS_CODE = "binding_evidence_too_many_words"
TOO_MANY_BYTES_CODE = "binding_evidence_too_many_bytes"
WORD_RULE = ("one word per run of alphanumeric characters, with an apostrophe or hyphen "
             "between two alphanumeric characters kept inside the run; one word per CJK, "
             "kana or Hangul character, counted individually so that unspaced text cannot "
             "exceed the budget silently; all other characters separate words and are not "
             "counted")

#: Code point ranges whose characters each count as one word.
INDIVIDUAL_RANGES = (
    (0x1100, 0x11FF),    # Hangul Jamo
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0x3100, 0x312F),    # Bopomofo
    (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0xA000, 0xA48F),    # Yi Syllables
    (0xAC00, 0xD7AF),    # Hangul Syllables
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0xFF66, 0xFF9D),    # Halfwidth Katakana
    (0x20000, 0x2A6DF),  # CJK Unified Ideographs Extension B
    (0x2A700, 0x2EBEF),  # CJK Extensions C-F
)
_JOINERS = ("'", "\u2019", "-", "\u2010")


def counts_individually(character):
    """Whether this character is counted as one word by itself."""
    point = ord(character)
    for low, high in INDIVIDUAL_RANGES:
        if low <= point <= high:
            return True
    return False


def count_words(text):
    """The word count of ``text`` under :data:`WORD_RULE`. Non-strings count as zero."""
    if not isinstance(text, str):
        return 0
    total = 0
    in_run = False
    length = len(text)
    for position, character in enumerate(text):
        if counts_individually(character):
            if in_run:
                total += 1
                in_run = False
            total += 1
            continue
        if character.isalnum():
            in_run = True
            continue
        if character in _JOINERS and in_run and position + 1 < length:
            following = text[position + 1]
            if following.isalnum() and not counts_individually(following):
                continue
        if in_run:
            total += 1
            in_run = False
    if in_run:
        total += 1
    return total


def measure(text, maximum_words=EVIDENCE_MAX_WORDS, maximum_bytes=EVIDENCE_MAX_BYTES):
    """Every number a reviewer needs, with the characters kept alongside the words.

    The character count stays in the report so that an old character-bound conclusion
    and this word-bound one can be told apart instead of being conflated.
    """
    encoded = text.encode("utf-8") if isinstance(text, str) else b""
    words = count_words(text)
    return {
        "word_budget_version": WORD_BUDGET_VERSION,
        "observed_words": words,
        "maximum_words": maximum_words,
        "observed_characters": len(text) if isinstance(text, str) else 0,
        "observed_bytes": len(encoded),
        "maximum_bytes": maximum_bytes,
        "rule": WORD_RULE,
        "cjk_kana_and_hangul_counted_per_character": True,
        "characters_are_reported_not_bounded": True,
        "byte_ceiling_is_not_a_word_count": True,
        "over_words": words > maximum_words,
        "over_bytes": len(encoded) > maximum_bytes,
        "limit_enforced": LIMIT_ENFORCED,
    }


def over_budget(text, maximum_words=EVIDENCE_MAX_WORDS, maximum_bytes=EVIDENCE_MAX_BYTES):
    """Whether ``text`` exceeds the word budget or the separate byte ceiling.

    Exactly ``maximum_words`` words passes; one more word fails.
    """
    report = measure(text, maximum_words, maximum_bytes)
    return report["over_words"] or report["over_bytes"]


def diagnostic(text, field, maximum_words=EVIDENCE_MAX_WORDS,
               maximum_bytes=EVIDENCE_MAX_BYTES):
    """A refusal record for an over-budget field, or ``None``.

    The offending text is never copied into the record and never truncated in place.
    """
    report = measure(text, maximum_words, maximum_bytes)
    if not (report["over_words"] or report["over_bytes"]):
        return None
    code = TOO_MANY_WORDS_CODE if report["over_words"] else TOO_MANY_BYTES_CODE
    return {"code": code, "field": field, "raw_truncated": False,
            "automatic_retry_permitted": False,
            "raw_response_retained": True, **report}


def sentence(field, report):
    """The one-line message used by both arms, so the two refusals read the same."""
    if report["over_words"]:
        return (field + " is " + str(report["observed_words"]) + " words and the limit is "
                + str(report["maximum_words"]) + " words; this field states the source facts "
                "the decision rests on and is not a place to deliberate or to reach a "
                "different conclusion than the structured fields record. The limit is a "
                "ceiling, not a length to fill. The raw response is retained unchanged.")
    return (field + " is " + str(report["observed_bytes"]) + " bytes and the separate byte "
            "ceiling is " + str(report["maximum_bytes"]) + " bytes. This ceiling refuses "
            "pathological input and is not a word count. The raw response is retained "
            "unchanged.")
