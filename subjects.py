"""
subjects.py

General ArchivesSpace `subject` resolver: reuse existing -> external
authority match -> local fallback, for any subject term_type and any
of several external authorities. This is genres.py's pattern
generalized -- genres.py's row-level genre resolution (typeOfResource
-> AAT) is now a thin wrapper around resolve_subject() here, and this
module also drives resource_builder.py's "added entry" subject fields
(topical/geographic/occupation/genre_form terms, sourced from LCSH/
TGM/RBMSCV/AAT/local).

authority values and what they mean:
  "aat"    -- Getty Art & Architecture Thesaurus (aat_client.py)
  "lcsh"   -- LC Subject Headings, via id.loc.gov/authorities/subjects.
              Also used for FAST- and occupation-labeled fields, by
              explicit decision: FAST terms overlap heavily with LCSH,
              and a real, separate FAST/LCDGT integration wasn't
              worth building for fields with no real data in them yet
              -- search LCSH and label the result "lcsh" instead of
              claiming a FAST/LCDGT match we never actually verified.
  "tgm"    -- Thesaurus for Graphic Materials, id.loc.gov/vocabulary/graphicMaterials
  "rbmscv" -- RBMS Controlled Vocabulary for Rare Materials Cataloging,
              id.loc.gov/vocabulary/rbmscv (covers the "rbgenr" scheme)
  "local"  -- no external lookup at all. The field's own name (e.g.
              a "...Local" suffixed field) already declares no
              authority is being claimed, so this skips straight to
              local, unreconciled creation -- deliberately not even
              attempting a lookup that might "upgrade" a term the
              archivist explicitly chose not to claim an authority for.

CAVEAT: same one genres.py already carried -- the `subject` schema's
exact placement of an external authority URI wasn't independently
verified against ArchivesSpace's live schema. Test a small real batch
before trusting this at scale.
"""

import json as _json

from aat_client import AATLookupError, find_conservative_match as aat_find_match, normalize as aat_normalize
from loc_client import (
    LCLookupError, find_conservative_match as loc_find_match, normalize as loc_normalize,
    SUBJECTS_PATH, TGM_PATH, RBMSCV_PATH,
)
from aspace_client import ArchivesSpaceError, parse_conflicting_record_uri

LOOKUP_ERRORS = (AATLookupError, LCLookupError)

_AUTHORITY_BASE_PATHS = {
    "lcsh": SUBJECTS_PATH,
    "tgm": TGM_PATH,
    "rbmscv": RBMSCV_PATH,
}


def _lookup(authority: str, name: str):
    if authority == "aat":
        return aat_find_match(name)
    if authority in _AUTHORITY_BASE_PATHS:
        return loc_find_match(name, base_path=_AUTHORITY_BASE_PATHS[authority])
    raise ValueError(f"Unknown authority: {authority!r} (expected aat/lcsh/tgm/rbmscv/local)")


def _normalize(authority: str, name: str) -> str:
    return aat_normalize(name) if authority == "aat" else loc_normalize(name)


def _search_existing_subject(client, term_name: str, authority: str):
    try:
        results = client.search({"q": f'"{term_name}"', "type[]": "subject", "page": 1})
    except Exception:  # noqa: BLE001
        return None
    target = _normalize(authority, term_name)
    for hit in results.get("results", []):
        title = hit.get("title") or ""
        if _normalize(authority, title) == target:
            return hit.get("uri")
    return None


def _search_subject_by_authority_id(client, uri: str):
    try:
        results = client.search({"q": f'"{uri}"', "type[]": "subject", "page": 1})
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
        if parsed.get("authority_id") == uri:
            return hit.get("uri")
    return None


def resolve_subject(term_name: str, term_type: str, authority: str, client, subject_cache: dict, log,
                     vocabulary_ref: str = "/vocabularies/1") -> dict:
    """Returns {"uri": <subject uri>, "status": <see below>} or None
    if term_name is blank.

    status is one of:
      "linked_existing"                 -- reused an existing subject, matched by term text
      "linked_existing_by_authority_id" -- reused a subject already authorized against this URI
      "linked_<authority>"              -- created a new subject authorized against that source
      "created_local"                   -- created a local subject (confirmed no match, or authority=="local")
      "created_local_<authority>_lookup_failed" -- created a local subject because the lookup
                                          itself failed (network/timeout) -- NOT a confirmed
                                          absence from that authority
    """
    if not term_name or not str(term_name).strip():
        return None

    name = str(term_name).strip()
    cache_key = (term_type, authority, _normalize(authority, name))
    if cache_key in subject_cache:
        return subject_cache[cache_key]

    existing_uri = _search_existing_subject(client, name, authority)
    if existing_uri:
        result = {"uri": existing_uri, "status": "linked_existing"}
        log(f'Subject "{name}" ({term_type}): reusing existing subject {existing_uri}')
        subject_cache[cache_key] = result
        return result

    match = None
    lookup_failed = False
    if authority != "local":
        try:
            match = _lookup(authority, name)
        except LOOKUP_ERRORS as exc:
            lookup_failed = True
            log(f'Subject "{name}" ({term_type}): {authority.upper()} lookup FAILED ({exc}) -- '
                f'this is a network/lookup failure, not a confirmed absence. Falling back to a '
                f'local subject; consider re-running or checking this term by hand.')

    if match:
        existing_by_authority = _search_subject_by_authority_id(client, match["uri"])
        if existing_by_authority:
            result = {"uri": existing_by_authority, "status": "linked_existing_by_authority_id"}
            log(f'Subject "{name}" ({term_type}): matched {authority.upper()} "{match["label"]}" '
                f'({match["uri"]}), already linked to subject {existing_by_authority} -- reusing it.')
            subject_cache[cache_key] = result
            return result

        payload = {
            "jsonmodel_type": "subject",
            "source": authority,
            "authority_id": match["uri"],
            "vocabulary": vocabulary_ref,
            "terms": [{
                "jsonmodel_type": "term",
                "term": match["label"],
                "term_type": term_type,
                "vocabulary": vocabulary_ref,
            }],
        }
        try:
            created = client.post("/subjects", payload)
        except ArchivesSpaceError as exc:
            conflict_uri = parse_conflicting_record_uri(str(exc), r"/subjects/\d+")
            if conflict_uri:
                result = {"uri": conflict_uri, "status": "linked_existing_by_authority_id"}
                log(f'Subject "{name}" ({term_type}): ArchivesSpace already had a subject for '
                    f'{authority.upper()} URI {match["uri"]} ({conflict_uri}) -- reusing it instead of failing.')
                subject_cache[cache_key] = result
                return result
            raise
        uri = created.get("uri")
        result = {"uri": uri, "status": f"linked_{authority}"}
        log(f'Subject "{name}" ({term_type}): matched {authority.upper()} "{match["label"]}" '
            f'({match["uri"]}) -> created subject {uri}')
        subject_cache[cache_key] = result
        return result

    # authority == "local", or an external lookup found no confident match, or the
    # lookup itself failed at the network level -- all three end up here.
    payload = {
        "jsonmodel_type": "subject",
        "source": "local",
        "vocabulary": vocabulary_ref,
        "terms": [{
            "jsonmodel_type": "term",
            "term": name,
            "term_type": term_type,
            "vocabulary": vocabulary_ref,
        }],
    }
    try:
        created = client.post("/subjects", payload)
    except ArchivesSpaceError as exc:
        conflict_uri = parse_conflicting_record_uri(str(exc), r"/subjects/\d+")
        if conflict_uri:
            result = {"uri": conflict_uri, "status": "linked_existing"}
            log(f'Subject "{name}" ({term_type}): ArchivesSpace already had a subject with this term '
                f'({conflict_uri}) -- likely a search-index lag from a very recent create on '
                f'an earlier row; reusing it instead of failing.')
            subject_cache[cache_key] = result
            return result
        raise
    uri = created.get("uri")
    if authority == "local":
        status = "created_local"
    else:
        status = f"created_local_{authority}_lookup_failed" if lookup_failed else "created_local"
    log(f'Subject "{name}" ({term_type}): '
        + ('local field, no lookup attempted' if authority == "local" else f'no confident {authority.upper()} match')
        + f' -> created local subject {uri}')
    result = {"uri": uri, "status": status}
    subject_cache[cache_key] = result
    return result
