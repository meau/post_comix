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

parse_target_reference() additionally recognizes archival object
references, including the staff UI's tree-view URL shape where the
path still says "resources" but the fragment names the actual
selected node:
  https://staff.example.org/repositories/2/archival_objects/396287
  https://staff.example.org/resources/1187/edit#tree::archival_object_396287
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
    archival object, paste its actual URL (staff, public, or API).

    The staff UI's tree view uses URLs shaped like
    ".../resources/1187/edit#tree::archival_object_396287" -- the
    PATH says "resources/1187" (just the page you're on) but the
    FRAGMENT after "#tree::" names the actual node selected in the
    tree, which is what the person means to target. That fragment is
    checked FIRST, before the plain path-based patterns, specifically
    so a resource-edit-view URL whose fragment points at a specific
    archival object is correctly read as targeting that object, not
    the resource the path happens to mention.
    """
    text = text.strip()
    if not text:
        raise ResourceUrlError("No resource/archival object URL or ID provided.")

    if text.isdigit():
        return "resource", text, None

    repo_match = re.search(r"/repositories/(\d+)", text)
    repository_id = repo_match.group(1) if repo_match else None

    # Tree-view fragment, e.g. "#tree::archival_object_396287" or
    # "#tree::resource_1187" -- takes priority over a path match.
    tree_ao_match = re.search(r"tree::archival_object_(\d+)", text)
    if tree_ao_match:
        return "archival_object", tree_ao_match.group(1), repository_id

    tree_resource_match = re.search(r"tree::resource_(\d+)", text)
    if tree_resource_match:
        return "resource", tree_resource_match.group(1), repository_id

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
