"""
resource_url.py

Accepts whatever the user pastes in for "which resource do these rows
go under" -- a staff-interface URL, a public-interface URL, a raw API
URI, or just a bare resource ID -- and extracts the resource ID (and,
if present, the repository ID, so we can sanity-check it against
secrets.json).

Examples all resolve to resource_id="123":
  https://staff.example.org/resources/123
  https://staff.example.org/resources/123#tree::resource_123
  https://staff.example.org/repositories/2/resources/123/edit
  https://example.org/repositories/2/resources/123          (public UI)
  https://example.org:8089/repositories/2/resources/123     (raw API)
  /repositories/2/resources/123
  123
"""

import re


class ResourceUrlError(Exception):
    pass


def parse_resource_reference(text: str):
    """Returns (resource_id: str, repository_id: str | None)."""
    text = text.strip()
    if not text:
        raise ResourceUrlError("No resource URL/ID provided.")

    if text.isdigit():
        return text, None

    resource_match = re.search(r"/resources/(\d+)", text)
    if not resource_match:
        raise ResourceUrlError(
            f"Couldn't find a resource ID in {text!r}. Paste the resource's "
            "staff URL, public URL, API URI, or just its numeric ID."
        )
    resource_id = resource_match.group(1)

    repo_match = re.search(r"/repositories/(\d+)", text)
    repository_id = repo_match.group(1) if repo_match else None

    return resource_id, repository_id


def build_resource_ref(repository_id: str, resource_id: str) -> str:
    return f"/repositories/{repository_id}/resources/{resource_id}"


def build_archival_object_ref(repository_id: str, archival_object_id: str) -> str:
    return f"/repositories/{repository_id}/archival_objects/{archival_object_id}"


def parse_target_reference(text: str):
    """Like parse_resource_reference, but accepts either a resource OR
    an archival_object URL/URI -- used by migrate.py, which can post
    spreadsheet rows as children of either kind of thing.

    Returns (kind, id, repository_id) where kind is "resource" or
    "archival_object", and repository_id may be None if not present
    in the given text.

    A bare number (no URL) is assumed to be a RESOURCE id, matching
    parse_resource_reference's prior behavior -- to target an
    archival object, paste its actual URL (staff, public, or API),
    which unambiguously contains "/archival_objects/".
    """
    text = text.strip()
    if not text:
        raise ResourceUrlError("No resource/archival object URL or ID provided.")

    if text.isdigit():
        return "resource", text, None

    repo_match = re.search(r"/repositories/(\d+)", text)
    repository_id = repo_match.group(1) if repo_match else None

    ao_match = re.search(r"/archival_objects/(\d+)", text)
    if ao_match:
        return "archival_object", ao_match.group(1), repository_id

    resource_match = re.search(r"/resources/(\d+)", text)
    if resource_match:
        return "resource", resource_match.group(1), repository_id

    raise ResourceUrlError(
        f"Couldn't find a resource or archival object ID in {text!r}. Paste the "
        "staff URL, public URL, API URI, or (for a resource) just its numeric ID."
    )
