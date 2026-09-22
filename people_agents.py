"""
people_agents.py

Resolves a personal name (e.g. an individual architect, "Last,
First") to an ArchivesSpace `agent_person` URI. Same three-tier
pattern as agents.py's publisher resolution -- reuse existing ->
conservative LC NAF match (rdftype=PersonalName) -> local DACS agent
-- with the same authority-ID collision guard.

Name parsing: spreadsheet values are expected as "Last, First" (per
the source templates seen so far, which say so explicitly in their
header instructions). Only the first comma is treated as the
surname/forename boundary -- "Smith, John, Jr." splits into primary
"Smith" and rest "John, Jr.", not three parts. A name with no comma
at all is treated as an unparsed single block in primary_name, since
DACS doesn't require inverted order and guessing which part is the
surname would be worse than leaving it whole.

CAVEAT: id.loc.gov's "known label" redirect lookup (in loc_client.py)
isn't restricted by rdftype -- it's a plain label match, so in
principle a personal name string could redirect to a corporate
heading that happens to share the exact text. This is a pre-existing
limitation carried over from the corporate-agent lookup, not new
here; the suggest2 fallback IS correctly filtered to PersonalName.
"""

import re

from aspace_client import ArchivesSpaceError, parse_conflicting_record_uri
from loc_client import LCLookupError, find_conservative_match, normalize as loc_normalize


def split_person_name(name: str):
    """"Last, First[, ...]" -> (primary_name, rest_of_name).
    No comma -> (name, "") -- left unparsed rather than guessed at.
    """
    if "," in name:
        primary, rest = name.split(",", 1)
        return primary.strip(), rest.strip()
    return name.strip(), ""


def _search_existing_person_agent(client, name: str):
    try:
        results = client.search({
            "q": f'"{name}"',
            "type[]": "agent_person",
            "page": 1,
        })
    except Exception:  # noqa: BLE001
        return None

    target = loc_normalize(name)
    for hit in results.get("results", []):
        title = hit.get("title") or hit.get("primary_name") or ""
        if loc_normalize(title) == target:
            return hit.get("uri")
    return None


def _search_person_agent_by_authority_id(client, lc_uri: str):
    import json as _json
    try:
        results = client.search({
            "q": f'"{lc_uri}"',
            "type[]": "agent_person",
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
        for name in parsed.get("names", []):
            if name.get("authority_id") == lc_uri:
                return hit.get("uri")
    return None


def _parse_conflicting_agent_uri(error_text: str):
    """See agents.py's identically-named function for why this exists
    -- same reasoning applies here (search-index lag vs. a genuine
    authority conflict), just for /agents/people instead of
    /agents/corporate_entities."""
    return parse_conflicting_record_uri(error_text, r"/agents/people/\d+")


def resolve_person_agent(name_raw: str, client, agent_cache: dict, log,
                          force_local: bool = False) -> dict:
    """Returns {"uri": <agent uri>, "status": <see agents.py's status
    list -- same meanings, "agent_person" instead of corporate>} or
    None if name_raw is blank.

    force_local: see agents.py's resolve_publisher_agent -- same
    reasoning, skips the LC NAF lookup entirely for a field that
    already declares no authority is being claimed.
    """
    if not name_raw or not str(name_raw).strip():
        return None

    name = str(name_raw).strip()
    cache_key = loc_normalize(name)
    if cache_key in agent_cache:
        return agent_cache[cache_key]

    existing_uri = _search_existing_person_agent(client, name)
    if existing_uri:
        result = {"uri": existing_uri, "status": "linked_existing"}
        log(f'Person "{name}": reusing existing ArchivesSpace agent {existing_uri}')
        agent_cache[cache_key] = result
        return result

    lc_match = None
    lc_lookup_failed = False
    if not force_local:
        try:
            lc_match = find_conservative_match(name, rdftype="PersonalName")
        except LCLookupError as exc:
            lc_lookup_failed = True
            log(f'Person "{name}": LC NAF lookup FAILED ({exc}) -- network/lookup failure, '
                f'not a confirmed absence from LC NAF. Falling back to a local agent.')

    if lc_match:
        existing_by_authority = _search_person_agent_by_authority_id(client, lc_match["uri"])
        if existing_by_authority:
            result = {"uri": existing_by_authority, "status": "linked_existing_by_authority_id"}
            log(f'Person "{name}": matched LC NAF "{lc_match["label"]}" ({lc_match["uri"]}), '
                f'already linked to agent {existing_by_authority} -- reusing it.')
            agent_cache[cache_key] = result
            return result

        primary, rest = split_person_name(lc_match["label"])
        payload = {
            "jsonmodel_type": "agent_person",
            "publish": True,
            "agent_type": "agent_person",
            "names": [{
                "jsonmodel_type": "name_person",
                "primary_name": primary,
                "rest_of_name": rest,
                "sort_name": lc_match["label"],
                "source": "naf",
                "authority_id": lc_match["uri"],
                "is_display_name": True,
                "name_order": "inverted" if rest else "direct",
            }],
        }
        try:
            created = client.post("/agents/people", payload)
        except ArchivesSpaceError as exc:
            conflict_uri = _parse_conflicting_agent_uri(str(exc))
            if conflict_uri:
                result = {"uri": conflict_uri, "status": "linked_existing_by_authority_id"}
                log(f'Person "{name}": ArchivesSpace already had an agent for LC URI '
                    f'{lc_match["uri"]} ({conflict_uri}) -- reusing it instead.')
                agent_cache[cache_key] = result
                return result
            raise
        uri = created.get("uri")
        result = {"uri": uri, "status": "linked_loc"}
        log(f'Person "{name}": matched LC NAF "{lc_match["label"]}" ({lc_match["uri"]}) -> created agent {uri}')
        agent_cache[cache_key] = result
        return result

    primary, rest = split_person_name(name)
    payload = {
        "jsonmodel_type": "agent_person",
        "publish": True,
        "agent_type": "agent_person",
        "names": [{
            "jsonmodel_type": "name_person",
            "primary_name": primary,
            "rest_of_name": rest,
            "sort_name": name,
            "source": "local",
            "rules": "dacs",
            "is_display_name": True,
            "name_order": "inverted" if rest else "direct",
        }],
    }
    try:
        created = client.post("/agents/people", payload)
    except ArchivesSpaceError as exc:
        conflict_uri = _parse_conflicting_agent_uri(str(exc))
        if conflict_uri:
            result = {"uri": conflict_uri, "status": "linked_existing"}
            log(f'Person "{name}": ArchivesSpace already had an agent with this name '
                f'({conflict_uri}) -- likely a search-index lag from a very recent create on '
                f'an earlier row; reusing it instead of failing.')
            agent_cache[cache_key] = result
            return result
        raise
    uri = created.get("uri")
    status = "created_local_lc_lookup_failed" if lc_lookup_failed else "created_local"
    reason = "explicitly local field, no lookup attempted" if force_local else "no ArchivesSpace or confident LC NAF match"
    log(f'Person "{name}": {reason} -> created local DACS agent {uri}')
    result = {"uri": uri, "status": status}
    agent_cache[cache_key] = result
    return result
