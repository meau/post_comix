"""
digital_objects.py

Handles the "this column is USUALLY more title text, but is
SOMETIMES actually a URL" pattern seen in Brown's publications sheet:
detects URL-shaped values and, instead of folding them into the
title, creates (or reuses) an ArchivesSpace `digital_object` record
and returns an instance dict linking it to the archival object.

Digital objects are looked up/deduplicated by URL (used as both the
digital_object_id and the file_version's file_uri) so the same URL
appearing on more than one row doesn't create duplicate records.
"""

import re

URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def looks_like_url(value) -> bool:
    if value is None:
        return False
    return bool(URL_RE.match(str(value).strip()))


def _search_existing_digital_object(client, url: str):
    try:
        results = client.search({
            "q": f'"{url}"',
            "type[]": "digital_object",
            "page": 1,
        })
    except Exception:  # noqa: BLE001
        return None
    for hit in results.get("results", []):
        if hit.get("digital_object_id") == url or url in (hit.get("title") or ""):
            return hit.get("uri")
    # Fall back to any single hit -- the query already scoped to this URL string.
    hits = results.get("results", [])
    if len(hits) == 1:
        return hits[0].get("uri")
    return None


def resolve_digital_object(url: str, title: str, client, digital_object_cache: dict, log) -> dict:
    """Returns {"uri": ..., "status": "linked_existing" | "created"}.
    `title` is used as the digital object's own title (falls back to
    the URL itself if blank).
    """
    url = str(url).strip()
    if url in digital_object_cache:
        result = dict(digital_object_cache[url])
        result["status"] = "reused_this_run"
        return result

    existing_uri = _search_existing_digital_object(client, url)
    if existing_uri:
        result = {"uri": existing_uri, "status": "linked_existing"}
        log(f'Digital object URL "{url}": reusing existing digital object {existing_uri}')
        digital_object_cache[url] = result
        return result

    payload = {
        "jsonmodel_type": "digital_object",
        "digital_object_id": url,
        "title": title or url,
        "publish": True,
        "file_versions": [{
            "jsonmodel_type": "file_version",
            "file_uri": url,
            "publish": True,
        }],
    }
    created = client.post("/digital_objects", payload)
    uri = created.get("uri")
    result = {"uri": uri, "status": "created"}
    log(f'Digital object URL "{url}": created digital object {uri}')
    digital_object_cache[url] = result
    return result


def build_digital_object_instance(digital_object_link: dict) -> dict:
    return {
        "jsonmodel_type": "instance",
        "instance_type": "digital_object",
        "digital_object": {"ref": digital_object_link["uri"]},
    }
