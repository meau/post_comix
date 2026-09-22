"""
agents.py

Resolves a publisher name (free text from the spreadsheet) to an
ArchivesSpace corporate-entity agent URI, in this order:

  1. Reuse an existing ArchivesSpace agent if one already matches by
     name.
  2. Otherwise, look it up in the LC Name Authority File (conservative
     match only -- see loc_client.py).
       - If ArchivesSpace already has an agent authorized against
         that exact LC URI (created from a *differently worded*
         spreadsheet publisher string that happens to be the same
         real-world publisher), reuse it rather than creating a
         second, duplicate authorized record.
       - Otherwise, create a new agent sourced from LC.
     If the LC lookup itself fails at the network level (rather than
     confidently finding no match), that's logged distinctly and
     treated as a fallback to step 3 -- NOT as proof the publisher
     isn't in LC NAF.
  3. Otherwise, create a local agent, described per DACS (Describing
     Archives: A Content Standard), since we're forming the name
     ourselves rather than citing an external authority.

Results are cached in-memory for the life of a run (agent_cache dict
passed in by the caller) so the same publisher is only resolved once
even though it appears on many rows.
"""

import json as _json
import re

from aspace_client import ArchivesSpaceError, parse_conflicting_record_uri
from loc_client import LCLookupError, find_conservative_match, normalize as loc_normalize


def _search_existing_agent(client, publisher_name: str):
    """Best-effort search of ArchivesSpace for an existing corporate
    agent whose primary name matches. Returns a URI or None.
    Never raises -- a search failure just means we fall through to
    LC lookup / local creation.
    """
    try:
        results = client.search({
            "q": f'"{publisher_name}"',
            "type[]": "agent_corporate_entity",
            "page": 1,
        })
    except Exception:  # noqa: BLE001
        return None

    target = loc_normalize(publisher_name)
    for hit in results.get("results", []):
        title = hit.get("title") or hit.get("primary_name") or ""
        if loc_normalize(title) == target:
            return hit.get("uri")
    return None


def _search_agent_by_authority_id(client, lc_uri: str):
    """Is there already an ArchivesSpace agent authorized against this
    exact LC URI (regardless of what spreadsheet string produced it)?
    Guards against two differently-worded publisher strings in the
    spreadsheet both resolving to the same real-world LC record and
    each trying to create their own authorized agent -- ArchivesSpace
    would reject the second create ("Authority ID must be unique"),
    so we check first and just reuse. Never raises.
    """
    try:
        results = client.search({
            "q": f'"{lc_uri}"',
            "type[]": "agent_corporate_entity",
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
    """If we race into ArchivesSpace's "must be unique" rejection --
    either the "Authority ID must be unique" case (a duplicate LC-
    linked create) or the plain "Agent must be unique" case (a
    duplicate local-name create, most often caused by ArchivesSpace's
    search index lagging behind a very recent write from an earlier
    row in this same run, so _search_existing_agent's check above
    didn't find it yet) -- pull the existing agent's URI out of the
    error so we can link to it instead of failing the whole row.
    """
    return parse_conflicting_record_uri(error_text, r"/agents/corporate_entities/\d+")


def resolve_publisher_agent(publisher_name: str, client, agent_cache: dict, log) -> dict:
    """Returns {"uri": <agent uri>, "status": <one of the strings below>}
    or None if publisher_name is blank.

    status is one of:
      "linked_existing"            -- reused an agent already in ArchivesSpace, matched by name
      "linked_existing_by_authority_id" -- reused an agent already authorized against this LC URI
      "linked_loc"                 -- created a new agent authorized against LC NAF
      "created_local"              -- created a new local/DACS agent (confirmed no LC NAF match)
      "created_local_lc_lookup_failed" -- created a new local/DACS agent because the LC NAF
                                          lookup itself failed (network/timeout) -- NOT a
                                          confirmed absence from LC NAF; worth a manual check
    """
    if not publisher_name or not str(publisher_name).strip():
        return None

    name = str(publisher_name).strip()
    cache_key = loc_normalize(name)

    if cache_key in agent_cache:
        return agent_cache[cache_key]

    # 1. Already in ArchivesSpace, matched by name?
    existing_uri = _search_existing_agent(client, name)
    if existing_uri:
        result = {"uri": existing_uri, "status": "linked_existing"}
        log(f'Publisher "{name}": reusing existing ArchivesSpace agent {existing_uri}')
        agent_cache[cache_key] = result
        return result

    # 2. Conservative LC NAF match?
    loc_match = None
    lc_lookup_failed = False
    try:
        loc_match = find_conservative_match(name)
    except LCLookupError as exc:
        lc_lookup_failed = True
        log(f'Publisher "{name}": LC NAF lookup FAILED ({exc}) -- this is a network/lookup '
            f'failure, not a confirmed absence from LC NAF. Falling back to a local agent; '
            f'consider re-running or checking this publisher by hand.')

    if loc_match:
        # Has some OTHER spreadsheet spelling already created an agent
        # authorized against this exact LC record? Reuse it rather
        # than creating a duplicate ArchivesSpace would reject anyway.
        existing_by_authority = _search_agent_by_authority_id(client, loc_match["uri"])
        if existing_by_authority:
            result = {"uri": existing_by_authority, "status": "linked_existing_by_authority_id"}
            log(f'Publisher "{name}": matched LC NAF "{loc_match["label"]}" '
                f'({loc_match["uri"]}), which is already linked to ArchivesSpace agent '
                f'{existing_by_authority} -- reusing it.')
            agent_cache[cache_key] = result
            return result

        payload = {
            "jsonmodel_type": "agent_corporate_entity",
            "publish": True,
            "agent_type": "agent_corporate_entity",
            "names": [{
                "jsonmodel_type": "name_corporate_entity",
                "primary_name": loc_match["label"],
                "sort_name": loc_match["label"],
                "source": "naf",
                "authority_id": loc_match["uri"],
                "is_display_name": True,
            }],
        }
        try:
            created = client.post("/agents/corporate_entities", payload)
        except ArchivesSpaceError as exc:
            conflict_uri = _parse_conflicting_agent_uri(str(exc))
            if conflict_uri:
                result = {"uri": conflict_uri, "status": "linked_existing_by_authority_id"}
                log(f'Publisher "{name}": ArchivesSpace already had an agent for LC URI '
                    f'{loc_match["uri"]} ({conflict_uri}) -- reusing it instead of failing.')
                agent_cache[cache_key] = result
                return result
            raise
        uri = created.get("uri")
        result = {"uri": uri, "status": "linked_loc"}
        log(f'Publisher "{name}": matched LC NAF "{loc_match["label"]}" '
            f'({loc_match["uri"]}) -> created agent {uri}')
        agent_cache[cache_key] = result
        return result

    # 3. Local, DACS-described agent.
    payload = {
        "jsonmodel_type": "agent_corporate_entity",
        "publish": True,
        "agent_type": "agent_corporate_entity",
        "names": [{
            "jsonmodel_type": "name_corporate_entity",
            "primary_name": name,
            "sort_name": name,
            "source": "local",
            "rules": "dacs",
            "is_display_name": True,
        }],
    }
    try:
        created = client.post("/agents/corporate_entities", payload)
    except ArchivesSpaceError as exc:
        conflict_uri = _parse_conflicting_agent_uri(str(exc))
        if conflict_uri:
            result = {"uri": conflict_uri, "status": "linked_existing"}
            log(f'Publisher "{name}": ArchivesSpace already had an agent with this name '
                f'({conflict_uri}) -- likely a search-index lag from a very recent create on '
                f'an earlier row; reusing it instead of failing.')
            agent_cache[cache_key] = result
            return result
        raise
    uri = created.get("uri")
    status = "created_local_lc_lookup_failed" if lc_lookup_failed else "created_local"
    log(f'Publisher "{name}": no ArchivesSpace or confident LC NAF match -> '
        f'created local DACS agent {uri}')
    result = {"uri": uri, "status": status}
    agent_cache[cache_key] = result
    return result
