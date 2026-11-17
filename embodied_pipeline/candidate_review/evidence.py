"""Per-condition source-fact review of one target phrase against the whole graph.

Pure functions. No file access, no network, no model call, no randomness. The only
inputs are the scene nodes, the topology overlay, the source edge list and the public
task text. Review-only files, reference answers and task ids never enter here.
"""

import copy
import re

from . import REVIEW_VERSION, MODE_EVIDENCE_ONLY, MODE_EVIDENCE_PLUS_COSINE
from ..contracts import text_budget
from ..topology_filter import region_phrase

MATCH = "match"
CONTRADICT = "contradict"
UNKNOWN = "unknown"
VERDICTS = (MATCH, CONTRADICT, UNKNOWN)

#: One candidate matched every asserted condition and no competitor survived.
AUTO_DISAMBIGUATED = "auto_disambiguated"
#: Two or more candidates match every asserted condition. The question is real.
GENUINE_AMBIGUITY = "genuine_ambiguity"
#: Nothing was contradicted away far enough to leave a single candidate.
INSUFFICIENT_EXCLUSION = "insufficient_exclusion"
#: Every candidate carries a contradiction, so the environment facts are not enough.
NEEDS_ENVIRONMENT_INFORMATION = "needs_environment_information"
#: The phrase, or a clause of it, is outside the grammar this reviewer supports.
REVIEW_NOT_SUPPORTED = "review_not_supported"
DECISIONS = (AUTO_DISAMBIGUATED, GENUINE_AMBIGUITY, INSUFFICIENT_EXCLUSION,
             NEEDS_ENVIRONMENT_INFORMATION, REVIEW_NOT_SUPPORTED)

#: Only this decision may change what a run does. Every other decision leaves the
#: model's own proposal, including its question, exactly as it was.
CHANGING_DECISIONS = (AUTO_DISAMBIGUATED,)

_WORD_RE = re.compile(r"[a-z0-9]+")
_ID_IN_TEXT_RE = re.compile(r"\bid\s*([0-9]{1,6})\b", re.IGNORECASE)

LEADING_ARTICLES = ("the ", "a ", "an ", "its ", "that ", "this ")

COLOUR_WORDS = {
    "white": "white", "black": "black", "grey": "grey", "gray": "grey",
    "silver": "silver", "chrome": "silver", "brown": "brown", "beige": "beige",
    "cream": "beige", "tan": "beige", "red": "red", "blue": "blue", "green": "green",
    "yellow": "yellow", "orange": "orange", "pink": "pink", "purple": "purple",
    "gold": "gold", "golden": "gold",
}
MATERIAL_WORDS = {
    "metal": "metal", "metallic": "metal", "steel": "metal", "aluminium": "metal",
    "aluminum": "metal", "chrome": "metal", "bronze": "bronze", "brass": "brass",
    "copper": "copper", "wood": "wood", "wooden": "wood",
    "plastic": "plastic", "glass": "glass", "fabric": "fabric", "cloth": "fabric",
    "upholstered": "fabric", "leather": "leather", "cardboard": "cardboard",
    "mesh": "mesh", "rubber": "rubber", "stone": "stone", "concrete": "concrete",
}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}

#: Nouns this reviewer is willing to treat as a head. An unrecognised head is never a
#: contradiction, only an ``unknown``, so an unfamiliar object cannot be excluded.
HEAD_NOUNS = frozenset((
    "table", "desk", "bench", "workbench", "counter", "countertop", "sideboard",
    "cabinet", "cupboard", "drawer", "dresser", "wardrobe", "locker", "bookcase",
    "shelf", "shelves", "shelving", "rack", "stand", "pedestal", "plinth", "easel",
    "sculpture", "ornament", "statue", "figurine", "vase", "plant", "pot",
    "chair", "stool", "sofa", "couch", "bed", "nightstand", "cushion", "pillow",
    "mirror", "whiteboard", "board", "poster", "picture", "frame", "clock",
    "cart", "trolley", "ladder", "toolbox", "tool", "tray", "bin", "tub", "bucket",
    "barrel", "drum", "box", "crate", "basket", "container", "can", "bottle",
    "cup", "mug", "bowl", "plate", "book", "case", "bag", "sign", "holder",
    "stanchion", "panel", "door", "window", "mat", "rug", "runner", "carpet",
    "dispenser", "printer", "monitor", "screen", "keyboard", "mouse", "speaker",
    "camera", "lamp", "light", "fan", "radiator", "heater", "pipe", "cone",
    "fridge", "refrigerator", "oven", "sink", "toilet", "bathtub", "shower",
    "towel", "cabinetry", "unit",
    "polyhedron", "prism", "cube", "sphere", "cylinder", "rod", "base", "leg",
    "top", "surface", "shelf", "lid", "handle", "wheel", "caster", "frame",
))
#: Words that may anchor a caption's own subject. A part word such as ``base`` is a
#: recognised head, so it still stops a modifier window, but it is never read as the
#: subject of a caption that also names a whole object.
SUBJECT_ANCHOR_EXCLUDED = frozenset((
    "base", "leg", "top", "surface", "lid", "handle", "wheel", "caster", "frame",
    "rod", "unit", "shelf",
))
#: Words that may precede a caption's subject without breaking the reading that the
#: following noun is that subject. A preposition in this position means the first noun
#: is not the subject, so the subject fallback is refused.
SUBJECT_PREFIX_ALLOWED = frozenset((
    "a", "an", "the", "this", "that", "one", "two", "three", "four", "five", "six",
    "small", "large", "tall", "short", "thin", "thick", "wide", "narrow", "long",
    "round", "square", "rectangular", "oval", "circular", "faceted", "geometric",
    "twisted", "spiral", "plain", "simple", "dark", "light", "pale", "bright",
    "matte", "glossy", "shiny", "worn", "old", "new", "modern", "vertical",
    "horizontal", "open", "closed", "empty", "full", "lidded", "cushioned",
))
#: Words that end a compound but do not themselves name the kind of thing.
GENERIC_TAIL = ("unit", "set", "piece", "item", "object", "assembly", "module")
PLURAL_HEADS = {"shelves": "shelf", "boxes": "box", "benches": "bench",
                "tables": "table", "chairs": "chair", "racks": "rack"}
#: Closed synonym groups. Grouping only ever creates a ``match``, never an exclusion,
#: so a generous group is the conservative direction.
HEAD_SYNONYM_GROUPS = (
    ("bench", "workbench"),
    ("shelf", "shelves", "shelving", "rack", "bookcase"),
    ("pedestal", "plinth"),
    ("sofa", "couch"),
    ("bin", "tub", "bucket"),
    ("cabinet", "cupboard", "cabinetry"),
    ("fridge", "refrigerator"),
    ("cart", "trolley"),
    ("board", "whiteboard"),
    ("monitor", "screen"),
    ("rug", "carpet", "mat", "runner"),
    ("sculpture", "statue", "figurine"),
)
#: Relation markers. ``supporting`` style markers put the candidate underneath; ``on``
#: and ``atop`` put the candidate on top of the other object. Direction is checked.
SUPPORT_MARKERS = ("supporting", "supports", "holding", "holds", "displaying",
                   "carrying", "topped with", "topped by")
SUPPORTED_BY_MARKERS = ("on top of", "atop", "on", "standing on", "resting on")
PART_MARKERS = ("with", "featuring", "having")
#: Heads that name a part of the object rather than a separate object standing on it.
#: ``with four shelves`` is a part clause; ``with the white geometric ornament`` is not,
#: and is read as a support relation that has to be backed by a recorded relation.
PART_HEADS = frozenset((
    "base", "leg", "top", "surface", "lid", "handle", "wheel", "caster", "frame",
    "rod", "shelf", "drawer", "door", "pane", "panel", "mesh", "cushion", "arm",
))
#: Relation kinds this reviewer will read as vertical support, after normalisation.
#: Anything else - ``near``, ``inside``, ``beside``, ``left of`` - is not support and
#: is never loaded into the support maps.
SUPPORT_RELATION_FORMS = {
    "on": "source_is_on_target",
    "on top of": "source_is_on_target",
    "atop": "source_is_on_target",
    "resting on": "source_is_on_target",
    "standing on": "source_is_on_target",
    "sitting on": "source_is_on_target",
    "placed on": "source_is_on_target",
    "mounted on": "source_is_on_target",
    "supports": "target_is_on_source",
    "supporting": "target_is_on_source",
    "holds": "target_is_on_source",
    "holding": "target_is_on_source",
    "carries": "target_is_on_source",
    "carrying": "target_is_on_source",
}
#: Words that are neither colour, material, number nor head. They are never dropped:
#: each becomes a descriptor condition that can match or stay unknown, never exclude.
DESCRIPTOR_STOPWORDS = frozenset((
    "a", "an", "the", "this", "that", "its", "of", "and", "or", "in", "on", "at",
    "to", "from", "for", "is", "be", "it", "one", "same", "other", "my", "your",
))
#: Clause openers this reviewer refuses to interpret. They become unsupported fragments,
#: which force an ``unknown`` rather than a guess.
UNSUPPORTED_MARKERS = ("where", "that", "which", "when", "while", "near", "beside",
                       "next to", "behind", "under", "underneath", "between",
                       "opposite", "across from", "left of", "right of")

NODE_PATH = "scene_graph.json#nodes[id={0}]"
OVERLAY_PATH = "topology_overlay.json#object_region_index[object_id={0}]"
EDGE_PATH = "scene_graph_edges.json#edges[{0}]"


class ReviewInputError(ValueError):
    """Raised when the scene inputs themselves are unusable, never for a weak phrase."""


def tokens(text):
    return _WORD_RE.findall(text.lower()) if isinstance(text, str) else []


def _strip_articles(text):
    lowered = text.strip()
    for article in LEADING_ARTICLES:
        if lowered.lower().startswith(article):
            return lowered[len(article):].strip()
    return lowered


def _synonym_group(head):
    for group in HEAD_SYNONYM_GROUPS:
        if head in group:
            return group
    return (head,)


def head_of(phrase):
    """The head noun of a noun phrase, with generic compound tails removed.

    ``metal shelving unit`` is ``shelving``; ``display pedestal`` is ``pedestal``;
    ``metallic spiral sculpture`` is ``sculpture``. The caller separates relation
    clauses before calling, so ``sculpture on pedestal`` never reaches here whole.
    """
    words = tokens(phrase)
    while words and words[-1] in GENERIC_TAIL and len(words) > 1:
        words = words[:-1]
    if not words:
        return None
    head = PLURAL_HEADS.get(words[-1], words[-1])
    return head


def head_is_recognized(head):
    return bool(head) and (head in HEAD_NOUNS or head in PLURAL_HEADS.values())


def _cut_clauses(text):
    """The subject segment of a noun phrase, with every trailing clause removed.

    ``wooden bench with metal frame`` is ``wooden bench``, so the head is the bench and
    not the frame it is described as having.
    """
    markers = tuple(SUPPORT_MARKERS) + tuple(SUPPORTED_BY_MARKERS) + tuple(PART_MARKERS) \
        + tuple(UNSUPPORTED_MARKERS)
    remainder = text if isinstance(text, str) else ""
    while remainder:
        split = _split_once(remainder, markers)
        if split is None:
            break
        remainder = split[0]
    return remainder.strip()


def label_head_of(text):
    """The head of a source label, read after its trailing clauses are cut away."""
    return head_of(_cut_clauses(text))


def _modifier_window(text, head):
    """Words that modify ``head`` where it occurs in ``text``, per occurrence.

    The window starts after the previous head noun, preposition or clause break, so a
    "white cabinet with a black base" gives ``{"white"}`` for ``cabinet`` and never
    carries ``black`` across the ``with``. A "cabinet with a white base" gives an empty
    window for ``cabinet``, so the white never becomes support for the cabinet itself.
    """
    words = tokens(text)
    group = set(_synonym_group(head)) | {head}
    stops = set(SUPPORT_MARKERS) | set(PART_MARKERS) | set(UNSUPPORTED_MARKERS)
    stops |= {"on", "in", "of", "at", "and", "or", "but", "to", "from", "atop", "under"}
    windows = []
    for position, word in enumerate(words):
        normalized = PLURAL_HEADS.get(word, word)
        if normalized not in group:
            continue
        window = []
        index = position - 1
        while index >= 0:
            previous = words[index]
            normalized_previous = PLURAL_HEADS.get(previous, previous)
            if previous in stops or normalized_previous in HEAD_NOUNS:
                break
            window.append(previous)
            index -= 1
        windows.append(set(window))
    return windows


def _subject_window(text):
    """The modifier window of a caption's own subject, when the grammar makes it clear.

    ``A white faceted polyhedron mounted on a circular black base`` has ``polyhedron``
    as its subject, reached across articles and plain adjectives only, so ``white``
    modifies the whole object and ``black`` stays with the ``base`` it follows.
    ``A bronze sculpture on a white base`` gives ``{"bronze"}`` for the subject, so a
    white main object is not supported. ``On the table sits a cup`` is refused, because
    the first noun is reached through a preposition and is not the subject.
    """
    words = tokens(text)
    allowed = set(SUBJECT_PREFIX_ALLOWED) | set(COLOUR_WORDS) | set(MATERIAL_WORDS) \
        | set(NUMBER_WORDS)
    for position, word in enumerate(words):
        normalized = PLURAL_HEADS.get(word, word)
        if normalized not in HEAD_NOUNS:
            continue
        if normalized in SUBJECT_ANCHOR_EXCLUDED:
            return None, None
        prefix = words[:position]
        if any(item not in allowed for item in prefix):
            return None, None
        return set(prefix), normalized
    return None, None


def _anchor_windows(text, head):
    """Modifier windows for ``head``, falling back to the caption's own subject.

    The fallback exists because a caption may name the same object with a different
    noun than the phrase does. It is used only where the grammar identifies a subject,
    and the anchor used is reported so a reader can check the reading.
    """
    windows = _modifier_window(text, head) if head else []
    if windows:
        return windows, "head_noun_occurrence"
    window, subject = _subject_window(text)
    if window is None:
        return [], "no_anchor"
    return [window], "caption_subject:" + subject


def _attribute_in_windows(windows, table):
    found = set()
    for window in windows:
        for word in window:
            if word in table:
                found.add(table[word])
    return found


def _counts_in_text(text, head):
    """``four shelves`` style counts, read only where the number precedes the noun."""
    words = tokens(text)
    group = set(_synonym_group(head)) | {head}
    found = set()
    for position, word in enumerate(words):
        if word not in NUMBER_WORDS:
            continue
        for offset in (1, 2):
            if position + offset >= len(words):
                break
            candidate = words[position + offset]
            if PLURAL_HEADS.get(candidate, candidate) in group:
                found.add(NUMBER_WORDS[word])
                break
    return found


def _split_once(text, markers):
    lowered = " " + text.lower() + " "
    best = None
    for marker in markers:
        index = lowered.find(" " + marker + " ")
        if index >= 0 and (best is None or index < best[0]):
            best = (index, marker)
    if best is None:
        return None
    index, marker = best
    return text[:index].strip(), marker, text[index + len(marker) + 1:].strip()


def parse_phrase(phrase):
    """Break one target phrase into the conditions this reviewer is willing to assert.

    Anything outside the supported grammar is recorded in ``unsupported_fragments``
    rather than guessed at, and an unsupported fragment keeps the whole review from
    claiming a recovery.

    Every modifier word is accounted for. A shape, size or ordering word this reviewer
    has no model for is not dropped: it becomes a descriptor condition, so a candidate
    the word does not describe cannot be reported as a full match.
    """
    record = {"phrase": phrase, "head": None, "head_recognized": False,
              "author_supplied_object_id": None, "colours": [], "materials": [],
              "counts": [], "relations": [], "unsupported_fragments": [],
              "descriptors": [], "room_qualifier": None, "supported": False}
    if not isinstance(phrase, str) or not phrase.strip():
        record["unsupported_fragments"].append("the target phrase is not a nonempty string")
        return record
    text = _strip_articles(phrase)
    id_match = _ID_IN_TEXT_RE.search(text)
    if id_match is not None:
        record["author_supplied_object_id"] = int(id_match.group(1))
        text = (text[:id_match.start()] + " " + text[id_match.end():])
    text = text.replace("(", " ").replace(")", " ")
    text = re.sub(r"\s+", " ", text).strip()
    remainder = text
    while remainder:
        split = _split_once(remainder, UNSUPPORTED_MARKERS)
        if split is None:
            break
        head_part, marker, tail = split
        record["unsupported_fragments"].append(marker + " " + tail)
        remainder = head_part
    relations = []
    while remainder:
        split = _split_once(remainder, SUPPORT_MARKERS)
        if split is None:
            break
        head_part, marker, tail = split
        relations.append({"direction": "candidate_supports_other", "marker": marker,
                          "other_phrase": _strip_articles(tail)})
        remainder = head_part
    split = _split_once(remainder, SUPPORTED_BY_MARKERS)
    if split is not None:
        head_part, marker, tail = split
        relations.append({"direction": "candidate_is_on_other", "marker": marker,
                          "other_phrase": _strip_articles(tail)})
        remainder = head_part
    parts = []
    while remainder:
        split = _split_once(remainder, PART_MARKERS)
        if split is None:
            break
        head_part, marker, tail = split
        parts.append(_strip_articles(tail))
        remainder = head_part
    head_segment = _strip_articles(remainder)
    record["head"] = head_of(head_segment)
    record["head_recognized"] = head_is_recognized(record["head"])
    record["head_segment"] = head_segment
    if record["head"]:
        windows = _modifier_window(head_segment, record["head"])
        record["colours"] = sorted(_attribute_in_windows(windows, COLOUR_WORDS))
        record["materials"] = sorted(_attribute_in_windows(windows, MATERIAL_WORDS))
        consumed = set(COLOUR_WORDS) | set(MATERIAL_WORDS) | set(NUMBER_WORDS) \
            | DESCRIPTOR_STOPWORDS | set(_synonym_group(record["head"])) \
            | {record["head"]}
        for word in tokens(head_segment):
            normalized = PLURAL_HEADS.get(word, word)
            if word in consumed or normalized in consumed:
                continue
            if normalized not in record["descriptors"]:
                record["descriptors"].append(normalized)
    for part in parts:
        part_head = head_of(part)
        counts = _counts_in_text(part, part_head) if part_head else set()
        if part_head and counts:
            record["counts"].append({"part_head": part_head, "count": sorted(counts)[0],
                                     "part_phrase": part})
        elif part_head in PART_HEADS:
            relations.append({"direction": "candidate_has_part", "marker": "with",
                              "other_phrase": part})
        elif part_head and head_is_recognized(part_head):
            # A separate object named in a "with" clause is a support claim about the
            # target, not a part of it, and needs a recorded relation to support it.
            relations.append({"direction": "candidate_supports_other", "marker": "with",
                              "other_phrase": part})
        else:
            record["unsupported_fragments"].append("with " + part)
    for relation in relations:
        other_head = head_of(relation["other_phrase"])
        relation["other_head"] = other_head
        relation["other_head_recognized"] = head_is_recognized(other_head)
        windows = _modifier_window(relation["other_phrase"], other_head) if other_head else []
        if not windows:
            window, subject = _subject_window(relation["other_phrase"])
            windows = [window] if window is not None else []
            relation["other_subject_anchor"] = subject
        relation["other_colours"] = sorted(_attribute_in_windows(windows, COLOUR_WORDS))
        relation["other_materials"] = sorted(_attribute_in_windows(windows, MATERIAL_WORDS))
        record["relations"].append(relation)
    record["supported"] = bool(record["head"]) and not record["unsupported_fragments"]
    return record


def _support_claims(text):
    """What a caption itself says this object supports, read only from stated verbs."""
    claims = []
    if not isinstance(text, str):
        return claims
    remainder = text
    while remainder:
        split = _split_once(remainder, SUPPORT_MARKERS)
        if split is None:
            break
        _head, marker, tail = split
        tail = re.split(r"[.,;]", tail)[0]
        other = _strip_articles(tail)
        other_head = head_of(other)
        if other_head:
            claims.append({"marker": marker, "other_phrase": other, "other_head": other_head,
                           "other_colours": sorted(_attribute_in_windows(
                               _modifier_window(other, other_head), COLOUR_WORDS))})
        remainder = tail
    return claims


def classify_edge(edge):
    """Normalise one source edge into ``upper on lower``, or refuse to read it.

    Only the relation forms in :data:`SUPPORT_RELATION_FORMS` are read as vertical
    support, and the direction is normalised rather than assumed from the field order.
    A proximity or containment relation such as ``near`` or ``inside`` is not support:
    it is recorded as unread with its own relation text, so a reader can see that this
    reviewer declined it instead of quietly treating it as ``on``.
    """
    source = edge.get("source")
    target = edge.get("target")
    relation = edge.get("relation")
    if source is None or target is None or not isinstance(relation, str):
        return None, "the edge has no usable source, target or relation text"
    words = tokens(relation)
    form = None
    matched = None
    for phrase, direction in SUPPORT_RELATION_FORMS.items():
        needle = tokens(phrase)
        for start in range(len(words) - len(needle) + 1):
            if words[start:start + len(needle)] == needle:
                if matched is None or len(needle) > len(tokens(matched)):
                    matched, form = phrase, direction
    if form is None:
        return None, "the relation " + repr(relation) + " is not a support relation"
    if form == "source_is_on_target":
        return {"upper": source, "lower": target, "form": matched}, None
    return {"upper": target, "lower": source, "form": matched}, None


def prepare_scene(nodes, overlay, edges=None):
    """Precompute the source facts once so a whole run reuses the same reading."""
    if not isinstance(nodes, list) or not nodes:
        raise ReviewInputError("A nonempty node list is required")
    index = overlay.get("object_region_index") if isinstance(overlay, dict) else None
    if not isinstance(index, list):
        raise ReviewInputError("The overlay must carry object_region_index")
    assignments = {entry.get("object_id"): entry for entry in index}
    rows = {}
    for node in nodes:
        object_id = node.get("id")
        entry = assignments.get(object_id) or {}
        label = node.get("object_tag") or ""
        caption = node.get("caption") or ""
        rows[object_id] = {
            "object_id": object_id,
            "object_tag": label,
            "caption": caption,
            "region_id": entry.get("region_id"),
            "region_status": entry.get("status") or "not_in_overlay_index",
            "active": node.get("active", True),
            "active_assumed_when_unspecified": "active" not in node,
            "label_head": label_head_of(label),
            "support_claims": _support_claims(caption),
        }
    supports = {}
    supported_by = {}
    edge_rows = []
    unread_edges = []
    for position, edge in enumerate(edges or []):
        source = edge.get("source")
        target = edge.get("target")
        relation = edge.get("relation")
        normalized, refusal = classify_edge(edge)
        edge_rows.append({"position": position, "source": source, "target": target,
                          "relation": relation,
                          "normalized_support": normalized,
                          "not_read_as_support_because": refusal})
        if normalized is None:
            unread_edges.append({"position": position, "relation": relation,
                                 "reason": refusal})
            continue
        # supports[x] = edges where x is the lower object, so x supports something.
        supports.setdefault(normalized["lower"], []).append(position)
        supported_by.setdefault(normalized["upper"], []).append(position)
    return {"rows": rows, "assignments": assignments, "edges": edge_rows,
            "supports": supports, "supported_by": supported_by,
            "edges_not_read_as_support": unread_edges,
            "support_relation_forms_read": sorted(set(SUPPORT_RELATION_FORMS)),
            "relation_set_is_not_claimed_complete": True,
            "region_vocabulary": region_phrase.region_vocabulary(overlay),
            # A read-only copy of the two documents the unchanged contract validator
            # needs. It is kept here so a hook can re-validate the model's own raw
            # proposal with the real validator instead of a cheaper look-alike check.
            # Nothing in this package mutates it, and it is not part of the reviewed
            # source facts above.
            "validator_inputs": {"nodes": copy.deepcopy(nodes),
                                 "overlay": copy.deepcopy(overlay)},
            "validator_inputs_are_read_only": True,
            "node_count": len(rows), "edge_count": len(edge_rows),
            "facts_used": ["object_tag", "caption", "overlay object_region_index",
                           "source edge list"],
            "model_evidence_text_used_as_source_fact": False,
            "basis": "unverified_source_annotations_not_ground_truth"}


def _condition(name, verdict, source_paths, detail):
    return {"condition": name, "verdict": verdict, "source_paths": list(source_paths),
            "detail": detail}


def _head_condition(parsed, row):
    paths = [NODE_PATH.format(row["object_id"]) + ".object_tag"]
    phrase_head = parsed["head"]
    label_head = row["label_head"]
    if phrase_head is None:
        return _condition("head_noun", UNKNOWN, paths, "the phrase has no readable head noun")
    if label_head is None:
        return _condition("head_noun", UNKNOWN, paths, "the label has no readable head noun")
    if label_head == phrase_head:
        return _condition("head_noun", MATCH, paths,
                          "the label head is " + repr(label_head))
    if label_head in _synonym_group(phrase_head):
        return _condition("head_noun", MATCH, paths,
                          "the label head " + repr(label_head) + " is in the closed synonym "
                          "group of " + repr(phrase_head))
    caption_paths = paths + [NODE_PATH.format(row["object_id"]) + ".caption"]
    caption_head_hits = [word for word in tokens(row["caption"])
                         if PLURAL_HEADS.get(word, word) in _synonym_group(phrase_head)]
    if caption_head_hits:
        return _condition("head_noun", UNKNOWN, caption_paths,
                          "the label head is " + repr(label_head) + " but the caption names "
                          + repr(sorted(set(caption_head_hits))) + ", which may describe a "
                          "different object in the same caption")
    if head_is_recognized(label_head) and head_is_recognized(phrase_head):
        return _condition("head_noun", CONTRADICT, paths,
                          "the label head " + repr(label_head) + " is a different recognised "
                          "kind of thing from " + repr(phrase_head))
    return _condition("head_noun", UNKNOWN, paths,
                      "one of the heads is outside the recognised noun list, so this cannot "
                      "be read as a contradiction")


def _attribute_condition(name, wanted, row, parsed, table):
    head = parsed["head"]
    label_windows, label_anchor = _anchor_windows(row["object_tag"], head)
    caption_windows, caption_anchor = _anchor_windows(row["caption"], head)
    label_found = _attribute_in_windows(label_windows, table)
    caption_found = _attribute_in_windows(caption_windows, table)
    found = label_found | caption_found
    paths = []
    if label_found:
        paths.append(NODE_PATH.format(row["object_id"]) + ".object_tag")
    if caption_found:
        paths.append(NODE_PATH.format(row["object_id"]) + ".caption")
    if not paths:
        paths = [NODE_PATH.format(row["object_id"]) + ".object_tag",
                 NODE_PATH.format(row["object_id"]) + ".caption"]
    wanted_set = set(wanted)
    anchors = {"object_tag": label_anchor, "caption": caption_anchor}
    if not found:
        return _condition(name, UNKNOWN, paths,
                          {"why": "no " + name + " is stated for the head noun or for the "
                                  "subject of either field; a value stated for a part is not "
                                  "read as the head's own " + name,
                           "anchors": anchors})
    if wanted_set & found:
        return _condition(name, MATCH, paths,
                          {"why": "the head noun is described as "
                                  + repr(sorted(wanted_set & found)),
                           "anchors": anchors})
    return _condition(name, CONTRADICT, paths,
                      {"why": "the head noun itself is described as " + repr(sorted(found))
                              + ", which excludes " + repr(sorted(wanted_set)),
                       "anchors": anchors,
                       "scope": "this is the candidate's own head noun, not a part of it"})


def _count_condition(wanted, row):
    part_head = wanted["part_head"]
    stated = set()
    paths = []
    for field in ("object_tag", "caption"):
        values = _counts_in_text(row[field], part_head)
        if values:
            stated |= values
            paths.append(NODE_PATH.format(row["object_id"]) + "." + field)
    name = "count_of_" + part_head
    if not paths:
        return _condition(name, UNKNOWN,
                          [NODE_PATH.format(row["object_id"]) + ".caption"],
                          "no count of " + repr(part_head) + " is stated")
    if wanted["count"] in stated:
        return _condition(name, MATCH, paths,
                          "the source states " + str(wanted["count"]) + " " + part_head)
    return _condition(name, CONTRADICT, paths,
                      "the source states " + repr(sorted(stated)) + " " + part_head
                      + ", not " + str(wanted["count"]))


#: The other object stated by a recorded relation is a different object from the one the
#: phrase names. On its own this is not an exclusion: the relation set is not complete.
MISMATCH = "mismatch"


def _texts_for(row):
    """The source fields of one node, read independently and each with its own path.

    They are never concatenated. A label that names the head without a colour must not
    stop the caption's own statement about that object from being read.
    """
    return (("object_tag", row.get("object_tag") or "",
             NODE_PATH.format(row.get("object_id")) + ".object_tag"),
            ("caption", row.get("caption") or "",
             NODE_PATH.format(row.get("object_id")) + ".caption"))


def _other_matches(other_head, other_colours, other_materials, text):
    """Whether one source field can be describing the relation's other object.

    Returns one of :data:`MATCH`, :data:`MISMATCH` or :data:`UNKNOWN` with a reason.
    ``MISMATCH`` means this particular stated text names something else. Nothing is
    excluded on that basis: see :func:`_relation_condition`.
    """
    if other_head is None:
        return UNKNOWN, "the phrase does not name the other object's kind"
    words = [PLURAL_HEADS.get(word, word) for word in tokens(text)]
    group = set(_synonym_group(other_head)) | {other_head}
    if not group & set(words):
        if head_is_recognized(other_head):
            return MISMATCH, ("the stated other object is not a " + str(other_head))
        return UNKNOWN, "the other object's kind is outside the recognised noun list"
    windows, anchor = _anchor_windows(text, other_head)
    for wanted, table, name in ((other_colours, COLOUR_WORDS, "colour"),
                                (other_materials, MATERIAL_WORDS, "material")):
        if not wanted:
            continue
        found = _attribute_in_windows(windows, table)
        if not found:
            return UNKNOWN, ("the other object's " + name + " is not stated for its own head "
                             "noun, anchor " + str(anchor))
        if not set(wanted) & found:
            return MISMATCH, ("the other object is " + repr(sorted(found)) + ", not "
                              + repr(sorted(wanted)))
    return MATCH, "the stated other object matches every asserted part of the phrase"


#: Two annotations of the same source object may be conjoined: one field names the
#: kind of thing, another states an attribute of that same object. The fields are
#: still read separately and are never concatenated; what joins them is the object id
#: they both annotate.
CROSS_FIELD_RULE = "same_source_object_label_category_plus_caption_subject_attribute"


def _states_category(other_head, text):
    """Whether this field's own subject segment names the kind the phrase asked for.

    The trailing clauses are cut away first, so a caption that mentions the kind only
    inside a clause about a *different* object - ``a white table supporting a bronze
    ornament`` - does not count as this object's category. Without that cut the
    conjunction below could take the category from one object and the colour from
    another and call the result a match.
    """
    if other_head is None:
        return False
    words = [PLURAL_HEADS.get(word, word) for word in tokens(_cut_clauses(text))]
    group = set(_synonym_group(other_head)) | {other_head}
    return bool(group & set(words))


def _subject_attributes(text):
    """The attributes a caption states about its own subject, or ``None``.

    Only the subject reading is used, which is what keeps a part's colour out of this:
    ``attached to a circular black base`` leaves ``black`` with the base, and a caption
    whose first noun is a part word is refused outright, so ``a white base`` can never
    make the whole object white.
    """
    window, subject = _subject_window(text)
    if window is None:
        return None
    return (_attribute_in_windows([window], COLOUR_WORDS),
            _attribute_in_windows([window], MATERIAL_WORDS), subject)


def _cross_field_match(wanted, entries):
    """Positive-only conjunction across the fields of one source object.

    The real shape this exists for: one field is a label that supplies the category and
    carries no colour of its own, and the same object's caption states the colour of its
    own subject using a different noun. Requiring both halves to appear in a single field
    rejected that evidence, even though the two fields are annotations of the same object
    and the phrase asserted nothing a source fact denied.

    Returns ``(reason, paths)`` or ``None``. It can only ever produce a match: when the
    caption says nothing about its subject, or says something else, this returns ``None``
    and the per-field verdicts stand. No exclusion is created here, so an unrecognised or
    disagreeing annotation still yields unknown rather than a denial.
    """
    other_head, colours, materials = wanted
    if other_head is None or not (colours or materials):
        return None
    category = [(field, path) for field, text, path in entries
                if _states_category(other_head, text)]
    if not category:
        return None
    for field, text, path in entries:
        if field != "caption":
            continue
        attributes = _subject_attributes(text)
        if attributes is None:
            continue
        found_colours, found_materials, subject = attributes
        agreed = []
        for want, found, name in ((colours, found_colours, "colour"),
                                  (materials, found_materials, "material")):
            if not want:
                continue
            if not found or not set(want) & found:
                return None
            agreed.append(name + " " + repr(sorted(set(want) & found)))
        if not agreed:
            return None
        paths = [item[1] for item in category]
        if path not in paths:
            paths.append(path)
        return (category[0][0] + " names the " + str(other_head) + " and the caption "
                "states " + ", ".join(agreed) + " for its own subject " + str(subject)
                + "; both fields annotate the same source object, so the conjunction is "
                "over one object's annotations and not over two objects", paths)
    return None


def _other_matches_fields(wanted, entries):
    """The best verdict across source fields read one at a time.

    ``MATCH`` from any single field wins, because a field that does describe the other
    object is positive evidence regardless of what a different field omits. Otherwise
    ``UNKNOWN`` is preferred over ``MISMATCH``, since a field naming something else is
    not a statement that the phrase's object is absent.

    When no single field carries every asserted part, the two fields of the same object
    are conjoined by :func:`_cross_field_match` before falling back. That step is
    positive-only and reports both source paths.
    """
    results = []
    for field, text, path in entries:
        verdict, reason = _other_matches(wanted[0], wanted[1], wanted[2], text)
        results.append((field, path, verdict, reason))
    hits = [item for item in results if item[2] == MATCH]
    if hits:
        return MATCH, hits[0][0] + ": " + hits[0][3], [item[1] for item in hits]
    conjoined = _cross_field_match(wanted, entries)
    if conjoined is not None:
        return MATCH, CROSS_FIELD_RULE + ": " + conjoined[0], conjoined[1]
    for target in (UNKNOWN, MISMATCH):
        hits = [item for item in results if item[2] == target]
        if hits:
            return target, hits[0][0] + ": " + hits[0][3], [item[1] for item in hits]
    return UNKNOWN, "no source field was readable", []


def _relation_readings(relation, row, scene):
    """Every recorded reading of this relation for this candidate, with its paths."""
    object_id = row["object_id"]
    direction = relation["direction"]
    forward = scene["supports"] if direction == "candidate_supports_other" \
        else scene["supported_by"]
    backward = scene["supported_by"] if direction == "candidate_supports_other" \
        else scene["supports"]
    wanted = (relation.get("other_head"), relation.get("other_colours") or [],
              relation.get("other_materials") or [])

    def other_of(position, flip):
        edge = scene["edges"][position]
        normalized = edge["normalized_support"] or {}
        if direction == "candidate_supports_other":
            key = "lower" if flip else "upper"
        else:
            key = "upper" if flip else "lower"
        return normalized.get(key)

    readings = []
    for position in forward.get(object_id, []):
        other = other_of(position, False)
        other_row = scene["rows"].get(other)
        if other_row is None:
            continue
        verdict, reason, paths = _other_matches_fields(wanted, _texts_for(other_row))
        readings.append({"basis": "edge", "verdict": verdict, "reason": reason,
                         "other_object_id": other, "direction": "as_stated",
                         "paths": [EDGE_PATH.format(position) + ".relation"] + paths})
    for position in backward.get(object_id, []):
        other = other_of(position, True)
        other_row = scene["rows"].get(other)
        if other_row is None:
            continue
        verdict, _reason, _paths = _other_matches_fields(wanted, _texts_for(other_row))
        if verdict == MATCH:
            readings.append({"basis": "edge", "verdict": CONTRADICT,
                             "reason": "the recorded relation between these two objects runs "
                                       "in the opposite direction to the phrase",
                             "other_object_id": other, "direction": "reversed",
                             "paths": [EDGE_PATH.format(position) + ".relation"]})
    if direction == "candidate_supports_other":
        for claim in row["support_claims"]:
            verdict, reason = _other_matches(wanted[0], wanted[1], wanted[2],
                                             claim["other_phrase"])
            readings.append({"basis": "caption_statement", "verdict": verdict,
                             "reason": reason, "other_object_id": None,
                             "direction": "as_stated",
                             "stated_other_phrase": claim["other_phrase"],
                             "paths": [NODE_PATH.format(object_id) + ".caption"]})
    return readings


#: No closed-world assumption is applied anywhere in this reviewer. A caption that says
#: an object supports one thing is not a statement that it supports nothing else, and
#: the supplied edge list is not a complete relation set. The consequence is accepted:
#: a relation can only ever be positive evidence here, or an explicit reversed-direction
#: contradiction, so strict evidence may yield no recovery at all. That is reported as an
#: unknown bottleneck rather than resolved by the program declaring a new fact.
NO_CLOSED_WORLD_ASSUMPTION = (
    "The relation set and the captions are not treated as complete. A stated support "
    "relation to one object never excludes another object, and a colour or relation that "
    "rules out one child never rules out the parent candidate as a whole."
)


def _relation_condition(relation, row, scene, settings):
    """A relation verdict that never reads an unrecorded relation as a denial.

    The only contradiction available here is an explicitly recorded relation between the
    same two objects running the other way. Everything else is positive evidence or
    unknown.
    """
    name = "relation_" + relation["direction"]
    readings = _relation_readings(relation, row, scene)
    paths = []
    for reading in readings:
        paths.extend(reading["paths"])
    detail = {"other_phrase": relation.get("other_phrase"), "readings": readings,
              "relation_set_is_not_claimed_complete": True,
              "closed_world_assumption_applied": False}
    if any(reading["verdict"] == MATCH for reading in readings):
        return _condition(name, MATCH, paths,
                          {"why": "a recorded relation connects this object to "
                                  + repr(relation.get("other_phrase"))
                                  + " in the stated direction", **detail})
    if any(reading["verdict"] == CONTRADICT for reading in readings):
        return _condition(name, CONTRADICT, paths,
                          {"why": "the only recorded relation between these objects runs in "
                                  "the opposite direction", **detail})
    return _condition(name, UNKNOWN, paths or [EDGE_PATH.format("none")],
                      {"why": "no recorded relation in the stated direction matches this "
                              "object. The supplied relation set is not complete, so this "
                              "is unrecorded rather than denied", **detail})


def _part_condition(relation, row):
    """A part is either named by the source text or unrecorded. It never excludes."""
    other_head = relation.get("other_head")
    name = "part_" + str(other_head)
    wanted = (other_head, relation.get("other_colours") or [],
              relation.get("other_materials") or [])
    verdict, reason, paths = _other_matches_fields(wanted, _texts_for(row))
    paths = paths or [NODE_PATH.format(row["object_id"]) + ".caption"]
    if verdict == MATCH:
        return _condition(name, MATCH, paths, "the source text names this part: " + reason)
    return _condition(name, UNKNOWN, paths,
                      "the source text does not establish this part; an unlisted part is "
                      "unrecorded rather than absent (" + reason + ")")


def _descriptor_condition(word, row):
    """A shape, size or ordering word this reviewer has no model for.

    It is never dropped and never used to exclude: a candidate the word does not
    describe is ``unknown``, which is enough to stop a full-match claim.
    """
    name = "descriptor_" + word
    paths = [NODE_PATH.format(row["object_id"]) + ".object_tag",
             NODE_PATH.format(row["object_id"]) + ".caption"]
    present = word in tokens(row["object_tag"]) or word in tokens(row["caption"])
    if present:
        return _condition(name, MATCH, paths,
                          "the source text uses the word " + repr(word))
    return _condition(name, UNKNOWN, paths,
                      "the source text does not use the word " + repr(word)
                      + "; this reviewer has no model for it, so it is unknown rather "
                        "than a contradiction")


def _room_condition(parsed, row, scene):
    qualifier = parsed.get("room_qualifier")
    if qualifier is None or not qualifier.get("recognized"):
        return None
    region_id = qualifier["region_id"]
    status = region_phrase.classify_candidate(region_id, row["object_id"],
                                              scene["assignments"])
    paths = [OVERLAY_PATH.format(row["object_id"]) + ".region_id"]
    if status == region_phrase.IN_DECLARED_REGION:
        return _condition("room", MATCH, paths,
                          "the overlay records this object in " + repr(region_id))
    if status == region_phrase.OTHER_KNOWN_REGION:
        return _condition("room", CONTRADICT, paths,
                          "the overlay records this object in " + repr(row["region_id"])
                          + ", not " + repr(region_id))
    return _condition("room", UNKNOWN, paths,
                      "the overlay records no room for this object, so the room qualifier "
                      "cannot exclude it")


def _author_id_condition(parsed, row):
    wanted = parsed.get("author_supplied_object_id")
    if wanted is None:
        return None
    paths = ["public_task_input.original_instruction"]
    if row["object_id"] == wanted:
        return _condition("author_supplied_scene_element_id", MATCH, paths,
                          "the user's own sentence names this scene element id")
    return _condition("author_supplied_scene_element_id", CONTRADICT, paths,
                      "the user's own sentence names scene element id " + str(wanted))


def evaluate_candidate(parsed, row, scene, settings=None):
    """Every asserted condition for one candidate, each with the path it was read from."""
    settings = dict(DEFAULT_CONFIG) if settings is None else settings
    conditions = [_head_condition(parsed, row)]
    author = _author_id_condition(parsed, row)
    if author is not None:
        conditions.append(author)
    if parsed["colours"]:
        conditions.append(_attribute_condition("colour", parsed["colours"], row, parsed,
                                               COLOUR_WORDS))
    if parsed["materials"]:
        conditions.append(_attribute_condition("material", parsed["materials"], row, parsed,
                                               MATERIAL_WORDS))
    for wanted in parsed["counts"]:
        conditions.append(_count_condition(wanted, row))
    for relation in parsed["relations"]:
        if relation["direction"] == "candidate_has_part":
            conditions.append(_part_condition(relation, row))
        else:
            conditions.append(_relation_condition(relation, row, scene, settings))
    for word in parsed["descriptors"]:
        conditions.append(_descriptor_condition(word, row))
    room = _room_condition(parsed, row, scene)
    if room is not None:
        conditions.append(room)
    verdicts = [item["verdict"] for item in conditions]
    if CONTRADICT in verdicts:
        verdict = CONTRADICT
    elif UNKNOWN in verdicts:
        verdict = UNKNOWN
    else:
        verdict = MATCH
    return {"object_id": row["object_id"], "object_tag": row["object_tag"],
            "region_id": row["region_id"], "region_status": row["region_status"],
            "active": row["active"], "conditions": conditions, "verdict": verdict,
            "contradicted_conditions": [item["condition"] for item in conditions
                                        if item["verdict"] == CONTRADICT],
            "unknown_conditions": [item["condition"] for item in conditions
                                   if item["verdict"] == UNKNOWN]}


def review_need(need, scene, mode=MODE_EVIDENCE_ONLY, config=None, cosine=None):
    """Review one target need against the whole graph and decide what, if anything, changed.

    ``need`` carries only public input and the model's own proposal:
    ``{"kind", "id", "phrase", "instruction", "proposal"}``. The proposal is reported back
    verbatim and its prose is never read as a source fact.

    The lexical cosine, when a mode asks for it, is reporting only. There is no option
    that lets a score change a decision: two candidates that both match every asserted
    condition stay a real ambiguity whatever their scores are.
    """
    settings = dict(DEFAULT_CONFIG)
    settings.update(config or {})
    phrase = need.get("phrase")
    parsed = parse_phrase(phrase)
    parsed["room_qualifier"] = region_phrase.analyze_role_text(
        phrase if isinstance(phrase, str) else "", scene["region_vocabulary"])
    proposal = need.get("proposal")
    evidence_text = ""
    if isinstance(proposal, dict) and isinstance(proposal.get("evidence"), str):
        evidence_text = proposal["evidence"]
    record = {
        "review_version": REVIEW_VERSION, "mode": mode,
        "need": {"kind": need.get("kind"), "id": need.get("id"), "phrase": phrase},
        "parsed": parsed,
        "scanned_node_count": scene["node_count"],
        "edge_count": scene["edge_count"],
        "candidate_rows": [], "head_contradicted_object_ids": [],
        "inactive_object_ids": [],
        "full_match_object_ids": [], "unknown_competitor_object_ids": [],
        "contradicted_object_ids": [],
        "decision": None, "decision_basis": None,
        "original_proposal": proposal,
        "original_evidence_chars": len(evidence_text),
        "original_evidence_budget": text_budget.measure(evidence_text),
        "original_evidence_budget_scope": "a measurement against the historical v1 word "
                                          "threshold, for reporting only; no length limit "
                                          "is enforced since evidence_audit_log_v1.",
        "model_evidence_text_used_as_source_fact": False,
        "source_facts_used": scene["facts_used"],
        "candidate_scope": "every node in the supplied graph, not only the ids the model "
                           "listed",
        "unmodelled_modifiers_kept_as_conditions": list(parsed["descriptors"]),
        "silently_ignored_modifiers": [],
        "assumptions": [],
        "closed_world_assumption_applied": False,
        "no_closed_world_assumption": NO_CLOSED_WORLD_ASSUMPTION,
        "cosine": None,
        "changed": False, "change_reason": None, "revised_proposal": None,
        "limitations": list(LIMITATIONS),
    }
    mentioned = set()
    if isinstance(proposal, dict):
        if isinstance(proposal.get("object_id"), int):
            mentioned.add(proposal["object_id"])
        for value in proposal.get("candidate_object_ids") or []:
            if isinstance(value, int):
                mentioned.add(value)
    for object_id in sorted(scene["rows"], key=lambda value: (value is None, value)):
        row = scene["rows"][object_id]
        if row["active"] is False:
            record["inactive_object_ids"].append(object_id)
            continue
        evaluation = evaluate_candidate(parsed, row, scene, settings)
        head_only = (evaluation["contradicted_conditions"] == ["head_noun"])
        if head_only and object_id not in mentioned:
            record["head_contradicted_object_ids"].append(object_id)
            continue
        record["candidate_rows"].append(evaluation)
        if evaluation["verdict"] == CONTRADICT:
            record["contradicted_object_ids"].append(object_id)
        elif evaluation["verdict"] == MATCH:
            record["full_match_object_ids"].append(object_id)
        else:
            record["unknown_competitor_object_ids"].append(object_id)
    full = record["full_match_object_ids"]
    unknown = record["unknown_competitor_object_ids"]
    if not parsed["supported"]:
        record["decision"] = REVIEW_NOT_SUPPORTED
        record["decision_basis"] = ("the phrase carries fragments outside the supported "
                                    "grammar: " + repr(parsed["unsupported_fragments"]))
    elif len(full) == 1 and not unknown:
        record["decision"] = AUTO_DISAMBIGUATED
        record["decision_basis"] = ("object " + str(full[0]) + " matches every asserted "
                                    "condition and no other candidate survived")
    elif len(full) >= 2:
        record["decision"] = GENUINE_AMBIGUITY
        record["decision_basis"] = ("objects " + repr(full) + " each match every asserted "
                                    "condition, so the question is supported by the source "
                                    "facts")
    elif full or unknown:
        record["decision"] = INSUFFICIENT_EXCLUSION
        record["decision_basis"] = ("competitors " + repr(unknown) + " are not excluded by "
                                    "any source fact, so no recovery is claimed")
    else:
        record["decision"] = NEEDS_ENVIRONMENT_INFORMATION
        record["decision_basis"] = "every candidate carries a contradiction with a named path"
    if mode == MODE_EVIDENCE_PLUS_COSINE and cosine is not None:
        record["cosine"] = cosine.report(phrase, record["candidate_rows"], scene)
        # By construction, not by configuration: there is no branch in which a score
        # changes the decision above. A removed option cannot be switched back on by a
        # caller passing a config key.
        record["cosine"]["may_change_a_decision"] = False
        record["cosine"]["changed_this_decision"] = False
        record["cosine"]["why"] = ("reporting only. An uncalibrated lexical score is not "
                                   "evidence about which object the user meant, so it never "
                                   "breaks a tie between two full matches and never forces a "
                                   "question")
    record["decision_may_change_the_run"] = record["decision"] in CHANGING_DECISIONS
    return record


#: Deliberately empty. There is no setting here that relaxes a contract or lets a score
#: bind a target. A raw proposal that fails the unchanged validator is never replaced,
#: and that is a property of the code path rather than a default a caller can flip: the
#: earlier ``allow_replacement_when_original_violates_contract`` switch was removed for
#: the same reason the cosine has no switch. The dict is kept so that a future, separately
#: reviewed option has an obvious place to live.
DEFAULT_CONFIG = {}

LIMITATIONS = (
    "Source labels, captions, the overlay room index and the edge list are unverified "
    "source annotations. They are not perception truth, not ground truth and not verified "
    "robot traversability.",
    "A recovery here is a binding recovery only. Nothing downstream of the binding was "
    "run, so no task is claimed complete.",
    "An unknown verdict means the source facts do not record the condition. It is never "
    "read as a denial and never as positive support.",
    "The model's own evidence prose is reported back verbatim and is never a source fact.",
    "The supplied relation set is not claimed to be complete. An unrecorded relation is "
    "never read as a denial, so a candidate is never excluded for missing an edge.",
    "The lexical cosine is reporting only. No configuration makes it change a decision.",
    "A raw proposal is only ever replaced when it already passes the unchanged binding "
    "validator on the real scene documents. A proposal that violates its own contract - a "
    "missing question, a malformed candidate list, an inactive id, an over-budget field - "
    "is reported with the validator's own refusal and travels on unchanged, so a source "
    "review can never wash out a contract defect. No option disables this.",
    "No closed-world assumption is applied. " + NO_CLOSED_WORLD_ASSUMPTION + " Strict "
    "evidence can therefore leave every question standing, and that is reported as an "
    "unknown bottleneck rather than resolved by declaring a new fact.",
)
