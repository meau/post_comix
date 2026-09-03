"""
agents.py

Resolves a publisher name (free text from the spreadsheet) to an
ArchivesSpace corporate-entity agent URI, in this order:

  1. Reuse an existing ArchivesSpace agent if one already matches.
  2. Otherwise, look it up in the LC Name Authority File (conservative
     match only -- see loc_client.py). If found, create an authorized
     agent sourced from LC.
  3. Otherwise, create a local agent, described per DACS (Describing
     Archives: A Content Standard), since we're forming the name
     ourselves rather than citing an external authority.

Results are cached in-memory for the life of a run (agent_cache dict
passed in by the caller) so the same publisher is only resolved once
even though it appears on many rows.
"""

from loc_client import find_conservative_match, normalize as loc_normalize


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


def resolve_publisher_agent(publisher_name: str, client, agent_cache: dict, log) -> dict:
    """Returns {"uri": <agent uri>, "status": <one of the strings below>}
    or None if publisher_name is blank.

    status is one of:
      "linked_existing" -- reused an agent already in ArchivesSpace
      "linked_loc"       -- created a new agent authorized against LC NAF
      "created_local"    -- created a new local/DACS-described agent
    """
    if not publisher_name or not str(publisher_name).strip():
        return None

    name = str(publisher_name).strip()
    cache_key = loc_normalize(name)

    if cache_key in agent_cache:
        return agent_cache[cache_key]

    # 1. Already in ArchivesSpace?
    existing_uri = _search_existing_agent(client, name)
    if existing_uri:
        result = {"uri": existing_uri, "status": "linked_existing"}
        log(f'Publisher "{name}": reusing existing ArchivesSpace agent {existing_uri}')
        agent_cache[cache_key] = result
        return result

    # 2. Conservative LC NAF match?
    loc_match = find_conservative_match(name)
    if loc_match:
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
        created = client.post("/agents/corporate_entities", payload)
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
    created = client.post("/agents/corporate_entities", payload)
    uri = created.get("uri")
    result = {"uri": uri, "status": "created_local"}
    log(f'Publisher "{name}": no ArchivesSpace or confident LC NAF match -> '
        f'created local DACS agent {uri}')
    agent_cache[cache_key] = result
    return result
