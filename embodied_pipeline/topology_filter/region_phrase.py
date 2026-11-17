"""Deterministic room qualifier recognition for prebinding role text.

Why this module exists
----------------------
An instruction may name the room itself: "the sign holder **in the hallway**".
That qualifier is information the user supplied, so the filter can check it
rather than leaving the model to argue with itself about whether a same-labelled
object in another room still competes. Everything here is computed from two
inputs only:

* the ``source_room_name`` values the overlay records for its regions, and
* the verbatim instruction / role text.

What this module deliberately does not do
-----------------------------------------
* It never looks at coordinates. A room is never re-guessed from geometry.
* It never invents a synonym. ``hallway`` is recognised because the overlay
  calls that region ``hallway``; ``corridor`` is not, even though the region
  type says corridor, because no such name was recorded.
* It never resolves which *object* is meant. It only narrows which candidates a
  room qualifier can and cannot exclude; deciding the object stays with the
  model and, afterwards, with the validator's own refusals.
* It recognises one small, closed grammar. Anything else - a negation, two room
  names, a room name that is not uniquely resolvable, a room name that is not
  the trailing phrase - is reported as *not recognised*, and the old unqualified
  path applies unchanged. Not recognising a qualifier is always the safe answer.
* It never turns "not recognised" into "not constrained". A wording this module
  cannot read leaves the restriction the user stated fully in force; the model
  is told to reason it out from the recorded facts as before, and that the
  grammar limit is this module's and not evidence that information is missing.

Two functions, two different jobs
---------------------------------
:func:`analyze_role_text` is the only source of a program-derived constraint. It
works on one role's own ``role_text`` and refuses negations, multiple room names
and non-trailing mentions.

:func:`instruction_lexical_room_mentions` is a *lexical scan* of the whole
instruction, used only to show the model where recorded room names appear in its
own text. It matches negated mentions just as readily as plain ones, so every
occurrence it reports carries a polarity and ``constraint_asserted: False``. It
must never be presented as a room fact.

The one asymmetry that matters: an object whose ``region_status`` is not
``assigned`` has **no recorded room**, so a room qualifier cannot exclude it. It
stays a possible candidate and is never silently turned into a known room.
"""

import re

#: Bumped from ``..._v1_20261104`` with three corrections: the whole-instruction
#: scan is reported as lexical occurrences with polarity instead of as asserted
#: qualifiers; room names that are not uniquely resolvable now count towards the
#: multiple-room refusal and contracted negations are detected; and an
#: unrecognised wording is stated to leave the user's own restriction in force.
QUALIFIER_VERSION = "topology_region_qualifier_v1_1_20261104"
#: The only prepositions read as a room qualifier. Closed list, never extended
#: by guessing: "near the hallway" and "past the hallway" are not containment.
PREPOSITIONS = ("in", "inside", "within")
#: Optional determiner between the preposition and the room name.
ARTICLES = ("the", "")
#: Articles stripped from the front of the remaining object phrase.
LEADING_ARTICLES = ("the ", "a ", "an ")
#: Any of these anywhere in the role text withdraws the reading. A qualifier that
#: excludes, negates or contrasts a room is not a plain containment statement, and
#: this module will not try to work out what it means.
EXCLUSION_WORDS = frozenset((
    "not", "no", "never", "non", "except", "excepting", "excluding", "exclude",
    "excluded", "outside", "other", "others", "otherwise", "unless", "neither",
    "nor", "instead", "rather", "but", "besides", "apart", "avoid", "avoiding",
    "skip", "skipping", "ignore", "ignoring", "without", "between", "either",
    "or", "both", "any"))
#: The same negations written without an apostrophe, plus a few more verbs that
#: reverse a containment claim. ``normalize`` drops apostrophes, so "isn't"
#: arrives as the two tokens "isn" and "t" and would otherwise slip through; the
#: verbatim probe below catches the contraction itself and these spellings catch
#: the forms that are written out.
NEGATIVE_WORDS = frozenset((
    "cannot", "isnt", "arent", "wasnt", "werent", "dont", "doesnt", "didnt",
    "wont", "wouldnt", "cant", "couldnt", "shouldnt", "mustnt", "neednt",
    "shant", "aint", "hasnt", "havent", "hadnt", "nothing", "none", "nobody",
    "nowhere", "omit", "omitting", "omitted", "disregard", "disregarding",
    "exclusive", "exclusively", "elsewhere"))
#: ``n't`` / ``n’t`` in the verbatim text. Normalisation splits the contraction
#: apart, so it has to be looked for before normalising.
NEGATION_CONTRACTION_RE = re.compile(r"[a-z]n['\u2019]t\b", re.IGNORECASE)
#: Clause boundaries used only to report which clause a room name was mentioned
#: in. This is reporting, never recognition: a clause reading is not a basis for
#: asserting a room constraint.
CLAUSE_SPLIT_RE = re.compile(
    r"[.;:,!?]|\b(?:and|then|but|while|or|nor|after|before|instead|however)\b",
    re.IGNORECASE)


def normalize(text):
    """Lowercase, punctuation-free, single-spaced form used for the word checks."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def negation_markers(text):
    """Every negation, exclusion or contrast marker in ``text``.

    Reads the verbatim string as well as its normalised form so that contracted
    negations such as ``isn't`` and ``isn’t`` are not lost to punctuation
    stripping. Finding a marker never means the module works out what was
    negated; it only means no plain containment reading may be taken.
    """
    found = {word for word in normalize(text).split()
             if word in EXCLUSION_WORDS or word in NEGATIVE_WORDS}
    if isinstance(text, str):
        found.update(match.group(0).lower().replace("\u2019", "'")
                     for match in NEGATION_CONTRACTION_RE.finditer(text))
    return sorted(found)


def _word_run_present(haystack, needle):
    """True when ``needle`` occurs in ``haystack`` as a whole word run."""
    if not needle:
        return False
    return (" " + haystack + " ").find(" " + needle + " ") >= 0


def _word_run_count(haystack, needle):
    if not needle:
        return 0
    padded, probe, count, start = " " + haystack + " ", " " + needle + " ", 0, 0
    while True:
        found = padded.find(probe, start)
        if found < 0:
            return count
        count += 1
        # Overlapping runs cannot share the trailing space, so step past the match
        # minus that space, which keeps "a a a" counted as two runs of "a a".
        start = found + len(probe) - 1


def _mentioned_independently(normalized, alias, other_aliases):
    """True when ``alias`` occurs somewhere a longer room name does not cover it.

    ``exhibition room`` is a word run inside ``exhibition room 2``, so a text
    saying "in the exhibition room 2" also literally contains the shorter name.
    Counting that as a second room name mentioned would refuse a perfectly
    explicit qualifier, so occurrences accounted for by a longer name that
    contains the shorter one are subtracted; only a leftover standalone mention
    counts as the shorter name being mentioned in its own right.
    """
    total = _word_run_count(normalized, alias)
    for other in other_aliases:
        if other == alias or not _word_run_present(other, alias):
            continue
        total -= _word_run_count(normalized, other) * _word_run_count(other, alias)
    return total > 0


def _blocked_aliases_mentioned(normalized, vocabulary, usable_matches):
    """Not-uniquely-resolvable room names this text mentions in their own right."""
    refused = [item["alias"] for item in vocabulary.get("refused") or []]
    candidates = sorted(alias for alias in refused
                        if _word_run_present(normalized, alias))
    longer = set(candidates) | set(usable_matches)
    return [alias for alias in candidates
            if _mentioned_independently(normalized, alias, longer)]


def region_vocabulary(overlay):
    """Usable room-name aliases from the overlay, plus the ones refused and why.

    An alias is usable only when it resolves to exactly one region. Two refusals
    are applied, both conservative:

    * two regions whose ``source_room_name`` normalises to the same words - the
      name does not identify a region at all;
    * a name that is a proper prefix of another region's name, such as
      ``exhibition room`` against ``exhibition room 2``. A person saying "the
      exhibition room" where three exist is not naming one room, so this module
      refuses the shorter name instead of preferring it.
    """
    by_alias = {}
    for region in overlay.get("regions") or []:
        alias = normalize(region.get("source_room_name"))
        if not alias:
            continue
        by_alias.setdefault(alias, []).append(region["region_id"])
    usable, refused = {}, []
    aliases = sorted(by_alias)
    for alias in aliases:
        region_ids = sorted(by_alias[alias])
        if len(region_ids) > 1:
            refused.append({"alias": alias, "region_ids": region_ids,
                            "reason": "two or more regions record this same room name, so the "
                                      "name does not identify one region"})
            continue
        longer = [other for other in aliases if other != alias
                  and other.startswith(alias + " ")]
        if longer:
            refused.append({"alias": alias, "region_ids": region_ids,
                            "reason": "this room name is a prefix of another room name "
                                      + repr(longer) + ", so on its own it does not single out "
                                      "one region",
                            "longer_room_names": longer})
            continue
        usable[alias] = {"region_id": region_ids[0],
                         "source_room_name": next(
                             region["source_room_name"]
                             for region in overlay["regions"]
                             if region["region_id"] == region_ids[0])}
    return {"qualifier_version": QUALIFIER_VERSION, "usable": usable, "refused": refused,
            "basis": "overlay_source_room_name_exact_alias_only"}


def qualifier_phrase_forms(alias):
    """Every surface form of the recognised grammar for one alias."""
    forms = []
    for preposition in PREPOSITIONS:
        for article in ARTICLES:
            parts = [preposition] + ([article] if article else []) + [alias]
            forms.append(" ".join(parts))
    return tuple(forms)


def _trailing_pattern(alias):
    """Regex for the qualifier as the *trailing* phrase of a text."""
    words = r"\s+".join(re.escape(word) for word in alias.split())
    return re.compile(r"\b(?P<preposition>" + "|".join(PREPOSITIONS) + r")\s+"
                      r"(?P<article>the\s+)?" + words + r"\s*[.,;:!?]?\s*$",
                      re.IGNORECASE)


def _anywhere_pattern(alias):
    words = r"\s+".join(re.escape(word) for word in alias.split())
    return re.compile(r"\b(?P<preposition>" + "|".join(PREPOSITIONS) + r")\s+"
                      r"(?P<article>the\s+)?" + words + r"\b", re.IGNORECASE)


def _unrecognized(role_text, reason, **extra):
    record = {"qualifier_version": QUALIFIER_VERSION, "role_text": role_text,
              "recognized": False, "reason": reason,
              "basis": "overlay_source_room_name_exact_alias_only",
              "preposition": None, "matched_text": None, "matched_alias": None,
              "region_id": None, "source_room_name": None, "object_phrase": None,
              "effect": "no room constraint is derived; this role is checked exactly as it was "
                        "before room qualifiers existed"}
    record.update(extra)
    return record


def analyze_role_text(role_text, vocabulary):
    """Whether this role text carries one explicit, unambiguous room qualifier.

    Recognition requires all of: no exclusion, negation or contrast marker
    anywhere, including contracted negations such as ``isn't``; exactly one room
    name mentioned, counting the names that are *not* uniquely resolvable
    towards that count, so "the shelf in the lab and the hallway" is refused
    even when only ``hallway`` resolves; that name mentioned exactly once; and
    it reached through one of the recognised prepositions as the trailing phrase
    of the text. The remaining words must be a nonempty object phrase. Anything
    else is refused with its reason recorded, and refusal is always safe: the
    role is then checked exactly as it was before this module existed.

    This function is the *only* place a program-derived room constraint comes
    from. A room name appearing somewhere in the instruction is not a constraint.
    """
    if not isinstance(role_text, str) or not role_text.strip():
        return _unrecognized(role_text, "role_text is not a nonempty string")
    usable = vocabulary.get("usable") or {}
    normalized = normalize(role_text)
    present = negation_markers(role_text)
    if present:
        return _unrecognized(role_text,
                             "an exclusion, negation or contrast marker is present, so this is "
                             "not a plain containment qualifier: " + repr(present),
                             exclusion_words_found=present)
    matched = sorted(alias for alias in usable if _word_run_present(normalized, alias))
    blocked = _blocked_aliases_mentioned(normalized, vocabulary, matched)
    # A name that cannot be resolved to one region is still a room name the text
    # mentions. Counting it keeps "the shelf in the lab and hallway", where
    # "lab" names two regions, from being read as a plain hallway qualifier. A
    # refused name that only appears as part of a longer name it is a prefix of
    # is not counted, so "in the exhibition room 2" stays recognisable.
    mentioned = sorted(set(matched) | set(blocked))
    if len(mentioned) > 1:
        return _unrecognized(role_text,
                             "more than one room name is mentioned, so no single room is named: "
                             + repr(mentioned), room_names_found=mentioned,
                             room_names_not_uniquely_resolvable_found=blocked)
    if not matched:
        if blocked:
            return _unrecognized(role_text,
                                 "the room name mentioned is not uniquely resolvable to one "
                                 "region: " + repr(blocked), room_names_found=blocked,
                                 room_names_not_uniquely_resolvable_found=blocked)
        return _unrecognized(role_text, "no room name recorded by the overlay appears in this "
                                        "role text")
    alias = matched[0]
    occurrences = _word_run_count(normalized, alias)
    if occurrences != 1:
        return _unrecognized(role_text,
                             "the room name appears " + str(occurrences) + " times; only a "
                             "single, trailing mention is read as a qualifier",
                             room_names_found=[alias], occurrences=occurrences)
    match = _trailing_pattern(alias).search(role_text)
    if match is None:
        return _unrecognized(role_text,
                             "the room name " + repr(alias) + " is present but not as a "
                             "recognised trailing qualifier " + repr(qualifier_phrase_forms(alias)),
                             room_names_found=[alias])
    prefix = role_text[:match.start()].strip().rstrip(",;:")
    lowered = prefix.lower()
    for article in LEADING_ARTICLES:
        if lowered.startswith(article):
            prefix = prefix[len(article):].strip()
            break
    if not prefix:
        return _unrecognized(role_text,
                             "the qualifier is the whole role text, so no object phrase is left "
                             "to resolve", room_names_found=[alias])
    entry = usable[alias]
    return {"qualifier_version": QUALIFIER_VERSION, "role_text": role_text,
            "recognized": True,
            "reason": "one recorded room name, mentioned once, reached through a recognised "
                      "preposition as the trailing phrase of the role text",
            "basis": "overlay_source_room_name_exact_alias_only",
            "preposition": match.group("preposition").lower(),
            "matched_text": match.group(0).strip(),
            "matched_alias": alias,
            "region_id": entry["region_id"],
            "source_room_name": entry["source_room_name"],
            "object_phrase": prefix,
            "effect": "candidates whose recorded room is a different region do not fit this "
                      "role; candidates with no recorded room cannot be excluded by it"}


def _clause_around(text, start, end):
    """The clause of ``text`` containing the span, cut at clause delimiters.

    Used only to report where a room name was mentioned. A clause reading is
    never a basis for asserting a constraint.
    """
    left, right = 0, len(text)
    for match in CLAUSE_SPLIT_RE.finditer(text):
        if match.end() <= start:
            left = match.end()
        elif match.start() >= end:
            right = match.start()
            break
    return text[left:right].strip()


PLAIN_MENTION = "plain_mention"
NEGATED_MENTION = "negated_or_contrasted_mention"


def instruction_lexical_room_mentions(instruction, vocabulary):
    """Where recorded room names literally occur in the verbatim instruction.

    This is a lexical scan and nothing more. It reports that the characters of a
    recorded room name appear after a containment preposition somewhere in the
    instruction. It does **not** decide that the instruction asserts that room,
    and it is deliberately not the function a constraint comes from - that is
    :func:`analyze_role_text`, applied per role, which refuses negations, two
    room names and non-trailing mentions.

    The distinction matters because this scan matches "do not look for it in the
    hallway" exactly as it matches "the sign holder in the hallway". Each
    occurrence therefore carries the clause it sits in, the negation or contrast
    markers found in that clause, and a ``polarity`` of :data:`PLAIN_MENTION` or
    :data:`NEGATED_MENTION`; and every occurrence carries
    ``constraint_asserted: False`` so that a negated hit can never be presented
    as a positive room fact.
    """
    usable = vocabulary.get("usable") or {}
    text = instruction if isinstance(instruction, str) else ""
    normalized = normalize(text)
    found = []
    for alias in sorted(usable):
        spans = list(_anywhere_pattern(alias).finditer(text))
        if not spans:
            continue
        clauses, markers = [], set()
        for match in spans:
            clause = _clause_around(text, match.start(), match.end())
            clauses.append(clause)
            markers.update(negation_markers(clause))
        found.append({"source_room_name": usable[alias]["source_room_name"],
                      "region_id": usable[alias]["region_id"],
                      "matched_alias": alias,
                      "matched_text": sorted({match.group(0).strip() for match in spans}),
                      "occurrences": len(spans),
                      "clauses": sorted(set(clauses)),
                      "negation_or_contrast_markers_in_clauses": sorted(markers),
                      "polarity": NEGATED_MENTION if markers else PLAIN_MENTION,
                      "constraint_asserted": False,
                      "note": "A lexical occurrence of a recorded room name. Whether a room "
                              "constraint applies is decided per role from that role's own "
                              "role_text, never from this list."})
    blocked = _blocked_aliases_mentioned(
        normalized, vocabulary, [item["matched_alias"] for item in found])
    return {"lexical_room_name_occurrences": found,
            "room_names_not_uniquely_resolvable_present": sorted(set(blocked)),
            "negation_or_contrast_markers_in_instruction": negation_markers(text),
            "status": "lexical_occurrences_only_no_room_constraint_asserted"}


#: Classification of one candidate against a declared room constraint.
IN_DECLARED_REGION = "in_the_declared_room"
OTHER_KNOWN_REGION = "recorded_in_a_different_room"
ROOM_UNKNOWN = "no_recorded_room_cannot_be_excluded"
ABSENT_FROM_INDEX = "absent_from_the_region_index"


def classify_candidate(region_id, object_id, assignments):
    """Where one object stands relative to a declared room, from the overlay only."""
    entry = assignments.get(object_id)
    if entry is None:
        return ABSENT_FROM_INDEX
    if entry.get("status") != "assigned":
        return ROOM_UNKNOWN
    return IN_DECLARED_REGION if entry.get("region_id") == region_id else OTHER_KNOWN_REGION


def classify_candidates(region_id, object_ids, assignments):
    """Group ids by :func:`classify_candidate`, preserving every id.

    Nothing is filtered out here. The caller gets the grouping and decides; in
    particular ``no_recorded_room_cannot_be_excluded`` is never merged into the
    declared room and never dropped.
    """
    groups = {IN_DECLARED_REGION: [], OTHER_KNOWN_REGION: [], ROOM_UNKNOWN: [],
              ABSENT_FROM_INDEX: []}
    for object_id in object_ids:
        groups[classify_candidate(region_id, object_id, assignments)].append(object_id)
    return {key: sorted(value) for key, value in groups.items()}


CONSTRAINT_RULES = (
    "A room qualifier is information the instruction itself supplies. When a role_text ends "
    "with one of the recognised qualifier forms listed here, that role is restricted to the "
    "named region.",
    "The lexical occurrence list below only records where a recorded room name appears in the "
    "instruction text. It asserts nothing. An occurrence marked "
    "'negated_or_contrasted_mention' is NOT a statement that the object is in that room, and "
    "an occurrence marked 'plain_mention' still only becomes a constraint for a role whose own "
    "role_text carries the qualifier.",
    "An object whose region_status is 'assigned' and whose region_id is a different region does "
    "NOT fit that role. Do not offer it as a candidate and do not call the role ambiguous "
    "because of it.",
    "An object whose region_status is not 'assigned' has no recorded room. A room qualifier "
    "cannot exclude it, so it may still fit, and it never becomes a known room.",
    "If exactly one object fits both the object words and the room constraint, the role is "
    "bound to it.",
    "If two or more objects still fit inside the named region, that is a real ambiguity: ask, "
    "stop the whole task, and do not pick one to make the document executable.",
    "Only the forms listed here are recognised by the filter. If the instruction phrases a "
    "restriction some other way, that means only that there is no PROGRAM-DERIVED constraint "
    "for it. It does not mean the restriction can be ignored: every restriction the "
    "instruction states still binds you, and you must resolve the role consistently with it.",
    "An unrecognised wording - a negation, several room names, a room this list does not "
    "record - is a limit of the filter's grammar and is NOT by itself evidence that "
    "information is missing. Reason about such a restriction yourself from the index as you "
    "would have before room qualifiers existed: if the recorded facts are enough to show that "
    "exactly one object satisfies what the user asked for, bind it.",
    "Report missing information and stop when the facts themselves are missing - the index "
    "records nothing that could settle the restriction, or more than one object still "
    "satisfies it and nothing recorded distinguishes them. Do not bind an object you cannot "
    "show satisfies the stated restriction, and do not quietly drop the restriction either.",
    "The attached index is complete. A room constraint narrows which candidates fit a role; it "
    "never removes an object from the index and never hides a competitor.",
)
CONSTRAINT_REFUSALS = (
    "Do not derive a room from coordinates, from the declared start region, or from which room "
    "would make the route convenient.",
    "Do not treat a room name this list does not contain as if it were recorded.",
    "Do not exclude an object because its room is unknown, and do not assign it a room.",
    "Do not resolve a genuine ambiguity in order to produce an executable document.",
    "Do not read a negated or contrasted mention of a room as a positive room fact, and do not "
    "invert it into a constraint on some other room.",
    "Do not treat 'no program-derived constraint' as 'no constraint'. An unrecognised wording "
    "leaves the user's own restriction in force.",
    "Do not treat 'the filter did not recognise this wording' as 'information is missing'. The "
    "grammar limit is the filter's, not the user's; decide from the recorded facts and stop "
    "only when those facts are genuinely absent or do not distinguish the candidates.",
)


def region_constraint_block(overlay, instruction):
    """The room-constraint rules block attached to the model request.

    What it carries is the recognised grammar, the recorded room-name vocabulary
    with its region ids, the rules and refusals, and a lexical scan showing
    where those names occur in the verbatim instruction. It carries no object
    id, no candidate list and no answer.

    It asserts no role constraint. A constraint is derived per role, from that
    role's own ``role_text`` via :func:`analyze_role_text`, and re-checked
    against the answer after it comes back. The lexical occurrences are
    orientation only: they include negated mentions, marked as such.
    """
    vocabulary = region_vocabulary(overlay)
    detected = instruction_lexical_room_mentions(instruction, vocabulary)
    return {
        "qualifier_version": QUALIFIER_VERSION,
        "derivation": "Computed by the filter from the overlay's recorded source_room_name "
                      "values and the verbatim instruction. You are not asked to infer a room "
                      "name. Any constraint is derived from an individual role_text, not from "
                      "the lexical occurrence list below, and the filter re-checks it against "
                      "your answer.",
        "basis": vocabulary["basis"],
        "recognized_grammar": {
            "prepositions": list(PREPOSITIONS),
            "article": "optional 'the'",
            "position": "the qualifier must be the trailing phrase of the role_text",
            "refused_shapes": ["a negation, exclusion or contrast word anywhere in the role text",
                               "two or more room names in one role text",
                               "a room name mentioned alongside a room name that is not "
                               "uniquely resolvable",
                               "a room name mentioned more than once",
                               "a room name that is not uniquely resolvable to one region",
                               "a room name that is not the trailing phrase"],
        },
        "region_name_vocabulary": [
            {"source_room_name": entry["source_room_name"], "region_id": entry["region_id"],
             "qualifier_phrase_forms": list(qualifier_phrase_forms(alias))}
            for alias, entry in sorted(vocabulary["usable"].items())],
        "room_names_not_uniquely_resolvable": list(vocabulary["refused"]),
        "instruction_lexical_room_name_occurrences":
            detected["lexical_room_name_occurrences"],
        "instruction_lexical_room_name_occurrence_count":
            len(detected["lexical_room_name_occurrences"]),
        "instruction_lexical_scan_status": detected["status"],
        "negation_or_contrast_markers_in_instruction":
            detected["negation_or_contrast_markers_in_instruction"],
        "lexical_occurrence_disclaimer":
            "The occurrence list is a lexical scan of the instruction text for recorded room "
            "names after a containment preposition. It is not a list of constraints and no "
            "entry in it asserts that an object is in a room. Negated and contrasted mentions "
            "appear in it too, marked with polarity, and every entry carries "
            "constraint_asserted: false.",
        "program_derived_role_constraints":
            "None are asserted in this block. The filter derives a room constraint only for a "
            "role whose own role_text ends with one of the recognised qualifier forms, and it "
            "re-checks that constraint against your answer. Where it derives nothing, any "
            "restriction the instruction states still applies to you unchanged.",
        "room_names_not_uniquely_resolvable_in_this_instruction":
            detected["room_names_not_uniquely_resolvable_present"],
        "rules": list(CONSTRAINT_RULES),
        "do_not": list(CONSTRAINT_REFUSALS),
        "region_annotation_status": "Source-asset geometry index, not a verified map and not a "
                                    "physical reachability claim.",
        "contains_object_ids": False,
        "contains_candidate_lists": False,
        "contains_reference_answer": False,
    }
