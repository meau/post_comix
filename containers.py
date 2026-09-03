"""
containers.py

Resolves a box number to an ArchivesSpace top_container URI, creating
one (type "box") the first time it's seen and reusing it after that.
Cached in-memory per run via container_cache, and also checks
ArchivesSpace itself first so re-running the script (or running it in
batches with --rows / --limit) doesn't create duplicate boxes.
"""


def _search_existing_top_container(client, indicator: str):
    try:
        results = client.search({
            "q": f'"{indicator}"',
            "type[]": "top_container",
            "filter_term[]": [f'{{"repository":"/repositories/{client.repository_id}"}}'],
            "page": 1,
        })
    except Exception:  # noqa: BLE001
        return None

    for hit in results.get("results", []):
        json_data = hit.get("json")
        hit_indicator = None
        hit_type = None
        if json_data:
            import json as _json
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


def resolve_top_container(box_number, client, container_cache: dict, log) -> dict:
    """box_number can be an int, float (e.g. 1.0), or string.
    Returns {"uri": ..., "status": "reused_existing" | "reused_this_run" | "created"}
    or None if box_number is blank.
    """
    if box_number is None or str(box_number).strip() == "":
        return None

    # Normalize "1.0" -> "1"
    try:
        indicator = str(int(float(box_number)))
    except (TypeError, ValueError):
        indicator = str(box_number).strip()

    if indicator in container_cache:
        result = dict(container_cache[indicator])
        result["status"] = "reused_this_run"
        return result

    existing_uri = _search_existing_top_container(client, indicator)
    if existing_uri:
        result = {"uri": existing_uri, "status": "reused_existing"}
        log(f'Box "{indicator}": reusing existing top container {existing_uri}')
        container_cache[indicator] = result
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
    container_cache[indicator] = result
    return result
