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
import re

from aspace_client import ArchivesSpaceError, parse_conflicting_record_uri


def normalize_barcode(barcode):
    """Barcodes arrive from Excel as floats (31236094336081.0) or
    strings; a trailing ".0" would never match what's actually on
    record. Returns a clean string, or None if blank."""
    if barcode is None:
        return None
    if isinstance(barcode, float) and barcode.is_integer():
        return str(int(barcode))
    text = str(barcode).strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    return text or None


def _find_container_by_barcode(client, barcode: str):
    """REPOSITORY-WIDE lookup of an existing top container by barcode
    (deliberately not scoped to any resource). Searches broadly by the
    barcode text, then verifies each candidate by fetching its actual
    record and comparing the barcode field exactly -- so this doesn't
    depend on guessing a search-index field name (the thing that broke
    resource-scoped matching). Returns (uri, record) or None. Never
    raises; a failed search just means "not found."
    """
    try:
        results = client.search({"q": f'"{barcode}"', "type[]": "top_container", "page": 1})
    except Exception:  # noqa: BLE001
        return None
    prefix = f"{client.repo_prefix}/top_containers/"
    for hit in results.get("results", []):
        uri = hit.get("uri")
        if not uri or not uri.startswith(prefix):
            continue  # a container in some other repository
        try:
            record = client.get(uri)
        except Exception:  # noqa: BLE001
            continue
        if record and str(record.get("barcode") or "").strip() == barcode:
            return uri, record
    return None


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

    return resolve_top_container_by_indicator(
        indicator, client, container_cache, resource_ref, log
    )


def resolve_top_container_by_indicator(indicator: str, client, container_cache: dict,
                                        resource_ref: str, log, barcode: str = None,
                                        container_type: str = "box", container_locations: list = None) -> dict:
    """Same resolution logic as resolve_top_container, but takes an
    already-computed indicator (and optional barcode/container_type/
    container_locations) directly -- for callers (like the
    generalized migrate.py) whose spreadsheet box values need
    config-driven parsing before they're a plain indicator string.
    See generic_builders.parse_box_value.

    container_locations (if given) is only ever applied when a NEW
    container is being created -- an already-existing, reused
    container's location is never touched here. Returns "created" in
    status either way so the caller can tell whether the location was
    actually attached (i.e. only check container_locations was used
    when status == "created").
    """
    if indicator is None or str(indicator).strip() == "":
        return None
    indicator = str(indicator).strip()
    barcode = normalize_barcode(barcode)

    # Cache key includes the resource AND type, so a placeholder
    # "folder" container never collides with a real "box" that
    # happens to share the same indicator text.
    cache_key = (resource_ref, container_type, indicator)
    barcode_key = ("barcode", barcode) if barcode else None

    # BARCODE WINS. A barcode identifies one physical container, so if
    # it already exists ANYWHERE in the repository (any resource, or
    # none) that container is linked and reused -- this is the one
    # exception to resource-scoped containers. Checked before the
    # resource-scoped lookups below.
    if barcode:
        warning = None
        cached = container_cache.get(barcode_key)
        if cached:
            uri, status = cached["uri"], "reused_this_run"
        else:
            found = _find_container_by_barcode(client, barcode)
            uri, status = (found[0], "reused_by_barcode") if found else (None, None)
            if found:
                record = found[1]
                rec_ind = str(record.get("indicator") or "").strip()
                rec_type = str(record.get("type") or "").strip()
                if rec_ind != indicator or (rec_type and rec_type != container_type):
                    warning = (
                        f'Box "{indicator}": barcode {barcode} already belongs to existing top '
                        f'container "{rec_type} {rec_ind}" ({uri}), not "{container_type} {indicator}" '
                        f'as this spreadsheet says -- reusing it anyway (barcode wins), but check '
                        f'the spreadsheet for a typo.'
                    )
                    log("WARNING: " + warning)
                log(f'Box "{indicator}": barcode {barcode} already exists in this repository as '
                    f'{uri} -- reusing it.')
                container_cache[barcode_key] = {"uri": uri, "status": "reused_by_barcode"}
        if uri:
            prior = container_cache.get(cache_key)
            if prior and prior["uri"] != uri:
                log(f'WARNING: Box "{indicator}": earlier rows for this box in this resource linked '
                    f'{prior["uri"]}, but barcode {barcode} points to {uri}. Check the barcode '
                    f'column for gaps or typos.')
            # Remember under the resource-scoped key too, so later rows
            # of this same box that lack a barcode land on the same container.
            container_cache[cache_key] = {"uri": uri, "status": "reused_by_barcode"}
            result = {"uri": uri, "status": status}
            if warning:
                result["warning"] = warning
            return result

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
        "type": container_type,
    }
    if barcode:
        payload["barcode"] = str(barcode)
    if container_locations:
        payload["container_locations"] = container_locations
    try:
        created = client.post(f"{client.repo_prefix}/top_containers", payload)
    except ArchivesSpaceError as exc:
        # Same reasoning as agents.py/genres.py: this is most often
        # ArchivesSpace's search index lagging behind a very recent
        # create from an earlier row, not a genuine data problem --
        # reuse the conflicting record rather than failing the row.
        conflict_uri = parse_conflicting_record_uri(str(exc), r"/top_containers/\d+")
        if not conflict_uri and barcode:
            # A duplicate-barcode rejection may not name the conflicting
            # record; the search index may also just have caught up since
            # our first look. Try the barcode lookup once more.
            found = _find_container_by_barcode(client, barcode)
            if found:
                conflict_uri = found[0]
        if conflict_uri:
            result = {"uri": conflict_uri, "status": "reused_existing"}
            log(f'Box "{indicator}": ArchivesSpace already had a top container matching this '
                f'({conflict_uri}) -- reusing it instead of failing.')
            container_cache[cache_key] = result
            if barcode:
                container_cache[barcode_key] = result
            return result
        raise
    uri = created.get("uri")
    result = {"uri": uri, "status": "created", "location_attached": bool(container_locations)}
    log(f'Box "{indicator}": created new top container {uri}'
        + (f' (barcode {barcode})' if barcode else '')
        + (' (location attached)' if container_locations else ''))
    container_cache[cache_key] = result
    if barcode:
        container_cache[barcode_key] = result
    return result
