"""
locations.py

Resolves shelf coordinates (e.g. Range 4 / Bay 2 / Shelf 5) to an
ArchivesSpace `location` record, by SEARCH ONLY -- this module never
creates a Location record. That's a deliberate choice: unlike top
containers, agents, or genre terms, a Location represents real
physical shelving infrastructure that shouldn't be improvised by a
migration script. An unmatched location is deferred instead -- see
missing_locations.py for the export/re-link workflow this feeds.

ArchivesSpace's location schema uses three optional coordinate
levels, each a (label, indicator) pair -- e.g.
("Range", "4"), ("Bay", "2"), ("Shelf", "5"). A location is matched
by comparing ALL non-blank coordinate levels (and building, if
given) against each candidate's full record, not just a fuzzy title
search.
"""


def _search_location(client, coordinates: dict, building: str):
    """coordinates: {1: ("Range","4"), 2: ("Bay","2"), 3: ("Shelf","5")}
    (only levels actually in use need to be present). Returns a URI
    or None. Never raises -- a search failure just means "not found,"
    which is the safe default (defer to the missing-locations export
    rather than risk a wrong reuse).
    """
    # Search on the most specific coordinate present -- most
    # selective, cuts down false candidates fastest.
    deepest_level = max(coordinates.keys())
    _, deepest_indicator = coordinates[deepest_level]

    try:
        results = client.search({
            "q": f'"{deepest_indicator}"',
            "type[]": "location",
            "page": 1,
        })
    except Exception:  # noqa: BLE001
        return None

    for hit in results.get("results", []):
        uri = hit.get("uri")
        if not uri:
            continue
        try:
            record = client.get(uri)
        except Exception:  # noqa: BLE001
            continue
        if not record:
            continue
        if building and (record.get("building") or "").strip().lower() != building.strip().lower():
            continue
        match = True
        for level, (label, indicator) in coordinates.items():
            rec_label = record.get(f"coordinate_{level}_label") or ""
            rec_indicator = record.get(f"coordinate_{level}_indicator") or ""
            if rec_label.strip().lower() != label.strip().lower() or rec_indicator.strip() != str(indicator).strip():
                match = False
                break
        if match:
            return uri
    return None


def resolve_location(coordinates: dict, building: str, client, location_cache: dict, log) -> dict:
    """coordinates: {1: ("Range","4"), 2: ("Bay","2"), 3: ("Shelf","5")}
    -- omit any level not in use.

    Returns {"uri": ..., "status": "linked_existing" | "reused_this_run"}
    if a match was found, or None if not (the caller is responsible
    for recording the miss -- see missing_locations.py).
    """
    if not coordinates:
        return None

    cache_key = (building, tuple(sorted(coordinates.items())))
    if cache_key in location_cache:
        cached = location_cache[cache_key]
        if cached is None:
            return None  # previously confirmed missing -- don't re-search
        result = dict(cached)
        result["status"] = "reused_this_run"
        return result

    uri = _search_location(client, coordinates, building)
    if uri:
        result = {"uri": uri, "status": "linked_existing"}
        coord_str = ", ".join(f"{label} {indicator}" for label, indicator in coordinates.values())
        log(f'Location ({coord_str}): matched existing location {uri}')
        location_cache[cache_key] = result
        return result

    location_cache[cache_key] = None
    return None


def build_container_locations(location_link: dict) -> list:
    if not location_link:
        return []
    import datetime
    return [{
        "jsonmodel_type": "container_location",
        "ref": location_link["uri"],
        "status": "current",
        "start_date": datetime.date.today().isoformat(),
    }]
