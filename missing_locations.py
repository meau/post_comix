"""
missing_locations.py

Two halves of the "location doesn't exist yet" workflow:

1. During a migrate.py run, every distinct coordinate combination
   that had no ArchivesSpace match gets collected (via record_miss)
   and, at the end of the run, written to an .xlsx for staff to
   review and create the real Location records from -- see
   write_missing_locations_report.

2. Every top_container that was created WITHOUT a location link
   (because its location was missing at the time) gets its own entry
   written to a JSON "pending relink" file -- see
   write_pending_relink and, for consuming it, relink_locations.py.
"""

import json
import os


def record_miss(missing: dict, coordinates: dict, building: str, title: str):
    """missing: a dict the caller owns across the whole run, passed
    in fresh (e.g. {}) at the start."""
    key = (building, tuple(sorted(coordinates.items())))
    if key not in missing:
        missing[key] = {"coordinates": coordinates, "building": building, "count": 0, "example_title": title}
    missing[key]["count"] += 1


def write_missing_locations_report(missing: dict, path: str):
    """Writes an .xlsx with one row per distinct missing location,
    ready for staff to fill in the ArchivesSpace URI once each one is
    created. Returns the path, or None if there was nothing to write.
    """
    if not missing:
        return None

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Missing Locations"
    ws.append(["Building", "Coordinate 1 label", "Coordinate 1", "Coordinate 2 label", "Coordinate 2",
               "Coordinate 3 label", "Coordinate 3", "Rows affected", "Example title",
               "ArchivesSpace Location URI (fill in once created)"])

    for entry in missing.values():
        coords = entry["coordinates"]
        row = [entry["building"] or ""]
        for level in (1, 2, 3):
            if level in coords:
                label, indicator = coords[level]
                row += [label, indicator]
            else:
                row += ["", ""]
        row += [entry["count"], entry["example_title"], ""]
        ws.append(row)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb.save(path)
    return path


def write_pending_relink(pending: list, path: str):
    """pending: list of {"top_container_uri", "building", "coordinates"}
    dicts, one per top_container created without a location link.
    Merges with any existing file at `path` rather than overwriting,
    so multiple runs accumulate rather than clobber each other.
    """
    existing = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)

    seen = {(e["top_container_uri"]) for e in existing}
    for entry in pending:
        if entry["top_container_uri"] not in seen:
            existing.append(entry)
            seen.add(entry["top_container_uri"])

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    return path


def coordinates_to_jsonable(coordinates: dict) -> dict:
    """{1: ("Range","4"), ...} -> {"1": ["Range","4"], ...} for JSON storage."""
    return {str(k): list(v) for k, v in coordinates.items()}


def coordinates_from_jsonable(data: dict) -> dict:
    return {int(k): tuple(v) for k, v in data.items()}
