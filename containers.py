"""
containers.py

Resolves a box number to an ArchivesSpace top_container URI, creating
one (type "box") the first time it's seen and reusing it after that.

Matching is scoped to the TARGET RESOURCE, not just the repository --
box "1" in this collection should never get linked to a top container
that indicator "1" happens to already point to in some *other*
resource in the same repository. Cached in-memory per run via
container_cache, and also checks ArchivesSpace itself first so
re-running the script (or running it in batches with --rows/--limit)
doesn't create duplicate boxes.

Resource scoping uses the search index's `collection_uri_u_sstr`
facet field, which ArchivesSpace populates on indexed top_container
records with the URI(s) of every resource that has an instance
pointing at that container (this is the same field the staff UI's
container-management "linked to this resource" filtering relies on).
If your ArchivesSpace instance indexes this differently, the search
will simply come back empty and the script will safely fall through
to *creating* a new container rather than risking a wrong match --
see the note in README.md.
"""

import json as _json


def _search_existing_top_container(client, indicator: str, resource_ref: str):
    try:
        results = client.search({
            "q": f'"{indicator}"',
            "type[]": "top_container",
            "filter_term[]": [
                f'{{"repository":"/repositories/{client.repository_id}"}}',
                f'{{"collection_uri_u_sstr":"{resource_ref}"}}',
            ],
            "page": 1,
        })
    except Exception:  # noqa: BLE001
        return None

    for hit in results.get("results", []):
        json_data = hit.get("json")
        hit_indicator = None
        hit_type = None
        if json_data:
            try:
                parsed = _json.loads(json_data)
                hit_indicator = parsed.get("indicator")
                hit_type = parsed.get("type")
            except Exception:  # noqa: BLE001
                pass
        if hit_indicator is None:
            hit_indicator = hit.get("indicator") or hit.get("display_string")
        if str(hit_indicator).strip() == str(indicator).strip() and (
            hit_type in (None, "box")
        ):
            return hit.get("uri")
    return None


def resolve_top_container(box_number, client, container_cache: dict, resource_ref: str, log) -> dict:
    """box_number can be an int, float (e.g. 1.0), or string.
    Returns {"uri": ..., "status": "reused_existing" | "reused_this_run" | "created"}
    or None if box_number is blank.

    Matching is scoped to resource_ref -- a top container with the same
    indicator that belongs to a *different* resource is never reused.
    """
    if box_number is None or str(box_number).strip() == "":
        return None

    # Normalize "1.0" -> "1"
    try:
        indicator = str(int(float(box_number)))
    except (TypeError, ValueError):
        indicator = str(box_number).strip()

    # Cache key includes the resource, so the same indicator in a
    # different resource (should this script ever be pointed at more
    # than one in a session) is never conflated.
    cache_key = (resource_ref, indicator)

    if cache_key in container_cache:
        result = dict(container_cache[cache_key])
        result["status"] = "reused_this_run"
        return result

    existing_uri = _search_existing_top_container(client, indicator, resource_ref)
    if existing_uri:
        result = {"uri": existing_uri, "status": "reused_existing"}
        log(f'Box "{indicator}": reusing existing top container {existing_uri} '
            f'(already linked to this resource)')
        container_cache[cache_key] = result
        return result

    payload = {
        "jsonmodel_type": "top_container",
        "indicator": indicator,
        "type": "box",
    }
    created = client.post(f"{client.repo_prefix}/top_containers", payload)
    uri = created.get("uri")
    result = {"uri": uri, "status": "created"}
    log(f'Box "{indicator}": created new top container {uri}')
    container_cache[cache_key] = result
    return result
