"""A TF-IDF cosine over source text. This is lexical similarity, not a semantic embedding.

Naming matters here, so it is stated plainly: this module compares word overlap, weighted
by how rare each word is in the supplied candidate texts. It has no notion that "rack" and
"shelving unit" mean the same thing unless both strings happen to share words. Calling such
a score a semantic embedding similarity would be false, and no sentence-transformer or other
embedding model is used, loaded or downloaded anywhere in this package.

Status of the numbers: uncalibrated. No threshold here has been fitted against a labelled
set, so every report says so. The scores are reporting only - :mod:`evidence` has no branch
in which a score changes a decision - and they exist so a reviewer can see what a similarity
ranking would have said next to what the source facts actually established.

What is compared: one auditable source card per candidate, built by :func:`source_card`.
A card carries the candidate's own label and caption, the overlay room name and the
aliases that resolve to exactly that region, and the support neighbours the source reader
accepted, each with an explicit direction and its own label and caption. Comparing only a
label against a relational phrase such as "the pedestal with the white ornament" would
have made the ranking meaningless for exactly the phrases that need it. Every card field
is marked with the source path it came from, and the card plus its digest are stored in
the report so a ranking can be reconstructed. Model evidence prose, candidate lists,
review-only files and reference answers are not inputs.

A local embedding provider is deliberately left as an explicit, inert seam:
:func:`local_embedding_provider` requires a caller-supplied local directory, refuses to
reach the network, and raises when the weights are not already present. The host checked
for this repository had only ``refs/main`` cached for the multilingual MiniLM model, with no
``snapshots`` or ``blobs`` directory, so no embedding backend was available and none was
fetched.
"""

import hashlib
import json
import math
import re

from . import REVIEW_VERSION

COSINE_VERSION = "tfidf_lexical_cosine_v1_20261107"
#: The shape of the auditable per-candidate source card the ranking is computed over.
CARD_VERSION = "candidate_source_card_v1_20261107"
#: Every field a card may carry, each one marked with the source path it came from.
#: ``object_tag`` and ``caption`` are the candidate's own text. ``source_room_name``
#: carries the overlay room name and the aliases that resolve to exactly that region.
#: The two relation fields are direction-explicit: ``supports_object`` is the neighbour
#: resting on the candidate, ``resting_on_object`` is what the candidate rests on. Only
#: the relation forms the source reader accepted as support are followed, and the
#: neighbour contributes its own ``object_tag`` and ``caption``.
CARD_FIELDS = ("object_tag", "caption", "source_room_name",
               "supports_object", "resting_on_object")
_NODE_PATH = "scene_graph.json#nodes[id={0}]"
_OVERLAY_PATH = "topology_overlay.json#object_region_index[object_id={0}]"
_EDGE_PATH = "scene_graph_edges.json#edges[{0}]"
#: The kind of similarity this module computes. Never abbreviated to "cosine similarity"
#: in a report without this qualifier.
COSINE_KIND = "tfidf_lexical_cosine_not_semantic_embedding"
EMBEDDING_BACKEND = "absent"
CALIBRATED = False
_WORD_RE = re.compile(r"[a-z0-9]+")


class EmbeddingUnavailable(RuntimeError):
    """Raised instead of downloading anything, or instead of guessing a score."""


def local_embedding_provider(model_dir, local_files_only=True):
    """An inert seam for a future local embedding backend. It never downloads.

    ``model_dir`` must be an explicit local directory that already contains weights.
    ``local_files_only`` must stay true: this function has no network path at all, and the
    argument exists so that a caller cannot ask for one without the request being visible.
    """
    if not local_files_only:
        raise EmbeddingUnavailable(
            "This provider has no network path. Remote weight fetching is not implemented "
            "and must not be added here without an explicit, separately approved decision.")
    raise EmbeddingUnavailable(
        "No local embedding weights are configured for " + repr(str(model_dir)) + ". A "
        "lexical TF-IDF cosine is used instead and is reported as lexical, never as a "
        "semantic embedding similarity.")


def _terms(text):
    return _WORD_RE.findall(text.lower()) if isinstance(text, str) else []


def _vector(terms, idf):
    counts = {}
    for term in terms:
        counts[term] = counts.get(term, 0) + 1
    weighted = {term: (1.0 + math.log(count)) * idf.get(term, 0.0)
                for term, count in counts.items()}
    norm = math.sqrt(sum(value * value for value in weighted.values()))
    if norm == 0.0:
        return {}, 0.0
    return weighted, norm


def _cosine(left, left_norm, right, right_norm):
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    shared = set(left) & set(right)
    total = sum(left[term] * right[term] for term in shared)
    return total / (left_norm * right_norm)


def _room_entries(scene, row):
    """Overlay room name and the aliases that resolve to exactly that region.

    An alias the overlay reader refused, such as a room name that is a prefix of another
    room name, is recorded on the card but kept out of the compared text: a name that does
    not single out one region is not evidence about this candidate either.
    """
    if row.get("region_status") != "assigned" or not row.get("region_id"):
        return [], []
    region_id = row["region_id"]
    vocabulary = scene.get("region_vocabulary") or {}
    included, excluded = [], []
    for alias, entry in sorted((vocabulary.get("usable") or {}).items()):
        if entry.get("region_id") != region_id:
            continue
        included.append({"field": "source_room_name", "alias": alias,
                         "text": entry.get("source_room_name") or alias,
                         "region_id": region_id,
                         "source_path": _OVERLAY_PATH.format(row["object_id"])})
    for entry in vocabulary.get("refused") or []:
        if region_id in (entry.get("region_ids") or []):
            excluded.append({"field": "source_room_name", "alias": entry.get("alias"),
                             "region_id": region_id,
                             "not_added_to_the_compared_text_because": entry.get("reason")})
    return included, excluded


def _relation_entries(scene, object_id):
    """Direction-explicit support neighbours, each contributing its own label and caption."""
    entries = []
    pairs = (("supports_object", "supports", "upper",
              "the neighbour rests on this candidate"),
             ("resting_on_object", "supported_by", "lower",
              "this candidate rests on the neighbour"))
    for field, adjacency, other_side, direction in pairs:
        for position in (scene.get(adjacency) or {}).get(object_id, []):
            edge = scene["edges"][position]
            other_id = (edge.get("normalized_support") or {}).get(other_side)
            other = (scene["rows"].get(other_id) or {})
            entries.append({
                "field": field, "direction": direction,
                "neighbour_object_id": other_id,
                "relation_as_recorded": edge.get("relation"),
                "neighbour_object_tag": other.get("object_tag") or "",
                "neighbour_caption": other.get("caption") or "",
                "text": ((other.get("object_tag") or "") + " "
                         + (other.get("caption") or "")).strip(),
                "source_path": _EDGE_PATH.format(position)})
    return entries


def source_card(object_id, scene):
    """The full, auditable text card one candidate contributes to the ranking.

    Only source documents are read: the node's own label and caption, the overlay room
    assignment and its aliases, and the support edges the source reader accepted. The
    model's evidence prose, its candidate list, any review-only file and any reference
    answer are not inputs here and never become card text.
    """
    row = scene["rows"].get(object_id) or {}
    fields = []
    for name in ("object_tag", "caption"):
        fields.append({"field": name, "text": row.get(name) or "",
                       "source_path": _NODE_PATH.format(object_id)})
    rooms, refused_rooms = _room_entries(scene, row)
    fields += rooms
    fields += _relation_entries(scene, object_id)
    text = " ".join(item["text"] for item in fields if item["text"]).strip()
    card = {
        "card_version": CARD_VERSION, "object_id": object_id,
        "fields": fields,
        "fields_excluded_from_the_compared_text": refused_rooms,
        "compared_text": text,
        "field_names_present": sorted({item["field"] for item in fields}),
        "reconstructable_from": list(CARD_FIELDS),
        "model_evidence_or_reference_text_used": False,
        "relation_set_is_not_claimed_complete": bool(
            scene.get("relation_set_is_not_claimed_complete")),
    }
    card["card_sha256"] = hashlib.sha256(
        json.dumps({key: value for key, value in card.items() if key != "card_sha256"},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return card


def report(phrase, candidate_rows, scene):
    """Rank the reviewed candidates by lexical cosine, with every caveat attached.

    The document corpus is the reviewed candidates' own source cards: label, caption,
    overlay room name and aliases, and direction-explicit support neighbours. A relation
    instruction such as "the pedestal with the white ornament" therefore has something to
    match against instead of silently comparing against the label alone. The idf weights
    depend on which candidates were reviewed, which is one more reason these numbers are a
    report and not a decision. Nothing below binds a target.
    """
    documents, cards = [], {}
    for row in candidate_rows:
        object_id = row["object_id"]
        card = source_card(object_id, scene)
        cards[object_id] = card
        documents.append((object_id, _terms(card["compared_text"])))
    total = len(documents)
    frequency = {}
    for _object_id, terms in documents:
        for term in set(terms):
            frequency[term] = frequency.get(term, 0) + 1
    idf = {term: math.log((1.0 + total) / (1.0 + count)) + 1.0
           for term, count in frequency.items()}
    query, query_norm = _vector(_terms(phrase), idf)
    ranked = []
    for object_id, terms in documents:
        vector, norm = _vector(terms, idf)
        ranked.append({"object_id": object_id,
                       "score": round(_cosine(query, query_norm, vector, norm), 6),
                       "shared_terms": sorted(set(terms) & set(query)),
                       "card_sha256": cards[object_id]["card_sha256"],
                       "card": cards[object_id]})
    ranked.sort(key=lambda row: (-row["score"], row["object_id"]))
    for position, row in enumerate(ranked, 1):
        row["rank"] = position
    margin = None
    if len(ranked) >= 2:
        margin = round(ranked[0]["score"] - ranked[1]["score"], 6)
    return {
        "review_version": REVIEW_VERSION,
        "cosine_version": COSINE_VERSION,
        "card_version": CARD_VERSION,
        "kind": COSINE_KIND,
        "is_semantic_embedding": False,
        "embedding_backend": EMBEDDING_BACKEND,
        "embedding_weights_present_locally": False,
        "downloads_attempted": 0,
        "calibrated": CALIBRATED,
        "calibration_note": "No threshold here was fitted against a labelled set. The "
                            "margin is reported and never acted on.",
        "corpus": "one source card per reviewed candidate: object_tag, caption, the "
                  "overlay room name and its usable aliases, and direction-explicit "
                  "support neighbours contributing their own object_tag and caption",
        "card_fields": list(CARD_FIELDS),
        "query_text": phrase,
        "query_terms": sorted(set(_terms(phrase))),
        "query_source": "the public task phrase under review, not a reference answer",
        "document_count": total,
        "ranked": ranked,
        "top_two_margin": margin,
        "may_change_a_decision": False,
    }
