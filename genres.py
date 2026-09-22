"""
genres.py

Resolves a free-text format/resource-type value (e.g. "Graphic
materials") to an ArchivesSpace `subject` record (term_type
genre_form), in this order -- deliberately mirroring agents.py's
publisher-resolution pattern:

  1. Reuse an existing ArchivesSpace subject if one already matches
     by term text.
  2. Otherwise, look it up in Getty AAT (conservative match only --
     see aat_client.py). If found, and no existing subject is already
     authorized against that exact AAT URI, create a new subject
     sourced from AAT.
  3. Otherwise, create a LOCAL subject (source: local) -- unlike
     publisher agents, there's no DACS-equivalent "how do I form a
     genre term myself" rule to cite, so this is just an unreconciled
     local term, clearly logged as such so it's easy to find and
     reconcile by hand later.

CAVEAT: the `subject` JSON schema's exact placement of an external
authority URI (unlike `agent` names, which have a documented
`authority_id` field) was not independently verified against
ArchivesSpace's live schema for this project -- test a couple of real
records with --dry-run and a small real batch before trusting this at
scale, the same caution that applies to the top-container
collection-scoping search elsewhere in this codebase.
"""

import json as _json

from aat_client import AATLookupError, find_conservative_match, normalize as aat_normalize
from aspace_client import ArchivesSpaceError, parse_conflicting_record_uri


def _search_existing_genre(client, term_name: str):
    try:
        results = client.search({
            "q": f'"{term_name}"',
            "type[]": "subject",
            "page": 1,
        })
    except Exception:  # noqa: BLE001
        return None

    target = aat_normalize(term_name)
    for hit in results.get("results", []):
        title = hit.get("title") or ""
        if aat_normalize(title) == target:
            return hit.get("uri")
    return None


def _search_subject_by_authority_id(client, aat_uri: str):
    try:
        results = client.search({
            "q": f'"{aat_uri}"',
            "type[]": "subject",
            "page": 1,
        })
    except Exception:  # noqa: BLE001
        return None

    for hit in results.get("results", []):
        json_data = hit.get("json")
        if not json_data:
            continue
        try:
            parsed = _json.loads(json_data)
        except Exception:  # noqa: BLE001
            continue
        if parsed.get("authority_id") == aat_uri:
            return hit.get("uri")
    return None


def resolve_genre_term(term_name: str, client, genre_cache: dict, log,
                        vocabulary_ref: str = "/vocabularies/1") -> dict:
    """Returns {"uri": <subject uri>, "status": <see below>} or None
    if term_name is blank.

    status is one of:
      "linked_existing"            -- reused an existing subject, matched by term text
      "linked_existing_by_authority_id" -- reused a subject already authorized against this AAT URI
      "linked_aat"                 -- created a new subject authorized against Getty AAT
      "created_local"              -- created a local, unreconciled subject (confirmed no AAT match)
      "created_local_aat_lookup_failed" -- created a local subject because the AAT lookup
                                          itself failed (network/timeout) -- NOT a confirmed
                                          absence from AAT
    """
    if not term_name or not str(term_name).strip():
        return None

    name = str(term_name).strip()
    cache_key = aat_normalize(name)
    if cache_key in genre_cache:
        return genre_cache[cache_key]

    existing_uri = _search_existing_genre(client, name)
    if existing_uri:
        result = {"uri": existing_uri, "status": "linked_existing"}
        log(f'Genre term "{name}": reusing existing subject {existing_uri}')
        genre_cache[cache_key] = result
        return result

    aat_match = None
    aat_lookup_failed = False
    try:
        aat_match = find_conservative_match(name)
    except AATLookupError as exc:
        aat_lookup_failed = True
        log(f'Genre term "{name}": AAT lookup FAILED ({exc}) -- this is a network/lookup '
            f'failure, not a confirmed absence from AAT. Falling back to a local subject; '
            f'consider re-running or checking this term by hand.')

    if aat_match:
        existing_by_authority = _search_subject_by_authority_id(client, aat_match["uri"])
        if existing_by_authority:
            result = {"uri": existing_by_authority, "status": "linked_existing_by_authority_id"}
            log(f'Genre term "{name}": matched AAT "{aat_match["label"]}" '
                f'({aat_match["uri"]}), already linked to subject {existing_by_authority} -- reusing it.')
            genre_cache[cache_key] = result
            return result

        payload = {
            "jsonmodel_type": "subject",
            "source": "aat",
            "authority_id": aat_match["uri"],
            "vocabulary": vocabulary_ref,
            "terms": [{
                "jsonmodel_type": "term",
                "term": aat_match["label"],
                "term_type": "genre_form",
                "vocabulary": vocabulary_ref,
            }],
        }
        try:
            created = client.post("/subjects", payload)
        except ArchivesSpaceError as exc:
            conflict_uri = parse_conflicting_record_uri(str(exc), r"/subjects/\d+")
            if conflict_uri:
                result = {"uri": conflict_uri, "status": "linked_existing_by_authority_id"}
                log(f'Genre term "{name}": ArchivesSpace already had a subject for AAT URI '
                    f'{aat_match["uri"]} ({conflict_uri}) -- reusing it instead of failing.')
                genre_cache[cache_key] = result
                return result
            raise
        uri = created.get("uri")
        result = {"uri": uri, "status": "linked_aat"}
        log(f'Genre term "{name}": matched AAT "{aat_match["label"]}" '
            f'({aat_match["uri"]}) -> created subject {uri}')
        genre_cache[cache_key] = result
        return result

    payload = {
        "jsonmodel_type": "subject",
        "source": "local",
        "vocabulary": vocabulary_ref,
        "terms": [{
            "jsonmodel_type": "term",
            "term": name,
            "term_type": "genre_form",
            "vocabulary": vocabulary_ref,
        }],
    }
    try:
        created = client.post("/subjects", payload)
    except ArchivesSpaceError as exc:
        conflict_uri = parse_conflicting_record_uri(str(exc), r"/subjects/\d+")
        if conflict_uri:
            result = {"uri": conflict_uri, "status": "linked_existing"}
            log(f'Genre term "{name}": ArchivesSpace already had a subject with this term '
                f'({conflict_uri}) -- likely a search-index lag from a very recent create on '
                f'an earlier row; reusing it instead of failing.')
            genre_cache[cache_key] = result
            return result
        raise
    uri = created.get("uri")
    status = "created_local_aat_lookup_failed" if aat_lookup_failed else "created_local"
    log(f'Genre term "{name}": no ArchivesSpace or confident AAT match -> created local subject {uri}')
    result = {"uri": uri, "status": status}
    genre_cache[cache_key] = result
    return result
