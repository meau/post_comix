"""
generic_builders.py

Config-driven versions of the archival_object field builders that
were originally hardcoded to Peck Comics' column names in
migrate_comics.py. Each function takes a raw row (as a tuple/list),
the sheet's header_index (name -> position, or None for headerless
sheets), and the relevant piece of a MappingConfig.

Date and extent logic is unchanged from the Peck Comics version --
date_parser.py and the extent/note shapes are already general-purpose,
they just weren't previously reachable from arbitrary column names.
"""

import re

from date_parser import parse_date_cell
from mapping import get_value


def clean_str(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_title(row_values, header_index, title_cfg):
    if not title_cfg:
        return "[Untitled]"
    return clean_str(get_value(row_values, header_index, title_cfg.get("column"))) or "[Untitled]"


def has_title(row_values, header_index, title_cfg):
    """Distinguishes a real file row from a pure hierarchy-header row
    (no file-level data) when walking a hierarchical sheet -- see
    hierarchy.py / migrate.py."""
    if not title_cfg:
        return False
    return bool(clean_str(get_value(row_values, header_index, title_cfg.get("column"))))


def build_title_and_digital_object(row_values, header_index, title_cfg, merge_cfg):
    """Handles the "second title column is USUALLY more title text,
    but is SOMETIMES a URL" pattern. Returns (title, url_or_None).
    When the merge column's value looks like a URL, it is NOT folded
    into the title -- the caller is expected to turn the URL into a
    digital_object instance instead (see digital_objects.py).
    """
    from digital_objects import looks_like_url

    base_title = build_title(row_values, header_index, title_cfg)
    if not merge_cfg:
        return base_title, None

    merge_val = clean_str(get_value(row_values, header_index, merge_cfg.get("column")))
    if not merge_val:
        return base_title, None

    if looks_like_url(merge_val):
        return base_title, merge_val

    separator = merge_cfg.get("separator", " ")
    return f"{base_title}{separator}{merge_val}", None


def build_extents(row_values, header_index, extent_cfg, warnings, title):
    if not extent_cfg:
        return []
    raw = get_value(row_values, header_index, extent_cfg.get("column"))
    if raw is None or str(raw).strip() == "":
        return []
    try:
        number = str(int(float(raw)))
    except (TypeError, ValueError):
        warnings.append(f'Title "{title}": extent value {raw!r} is not numeric -- extent skipped.')
        return []
    return [{
        "jsonmodel_type": "extent",
        "portion": "whole",
        "number": number,
        "extent_type": extent_cfg.get("extent_type", "items"),
    }]


def build_scope_notes(row_values, header_index, scope_note_cfgs):
    notes = []
    for cfg in scope_note_cfgs or []:
        val = clean_str(get_value(row_values, header_index, cfg.get("column")))
        if not val:
            continue
        content = (cfg.get("prefix") or "") + val + (cfg.get("suffix") or "")
        if cfg.get("ensure_trailing_period") and not content.endswith("."):
            content += "."
        notes.append({
            "jsonmodel_type": "note_multipart",
            "type": "scopecontent",
            "publish": True,
            "subnotes": [{
                "jsonmodel_type": "note_text",
                "content": content,
                "publish": True,
            }],
        })
    return notes


def build_physdesc_note(row_values, header_index, physdesc_cfg):
    if not physdesc_cfg:
        return []
    val = clean_str(get_value(row_values, header_index, physdesc_cfg.get("column")))
    if not val:
        return []
    return [{
        "jsonmodel_type": "note_singlepart",
        "type": "physdesc",
        "publish": True,
        "content": [val],
    }]


def build_notes(row_values, header_index, mapping, warnings):
    return (
        build_scope_notes(row_values, header_index, mapping.scope_notes)
        + build_physdesc_note(row_values, header_index, mapping.physdesc)
    )


def build_dates(row_values, header_index, date_cfg, warnings, title):
    if not date_cfg:
        return []
    raw = get_value(row_values, header_index, date_cfg.get("column"))
    if raw is None or str(raw).strip() == "":
        return []
    parsed = parse_date_cell(raw)
    if parsed is None:
        warnings.append(f'Title "{title}": could not parse date {raw!r} -- date subrecord skipped.')
        return []
    date_record = {
        "jsonmodel_type": "date",
        "label": date_cfg.get("label", "publication"),
        "date_type": parsed.date_type,
        "expression": parsed.expression,
        "begin": parsed.begin,
    }
    if parsed.end:
        date_record["end"] = parsed.end
    return [date_record]


def get_box_barcode_from_column(row_values, header_index, box_cfg):
    """Some templates store the container's barcode in its own column
    (shared identically across every row of the same box), rather than
    embedded in the box value itself. Only meaningful at container
    CREATION time -- resolve_top_container_by_indicator ignores the
    barcode argument when reusing an existing container, so this never
    overwrites a barcode already on record.
    """
    if not box_cfg or not box_cfg.get("barcode_column"):
        return None
    return clean_str(get_value(row_values, header_index, box_cfg["barcode_column"]))


def parse_box_value(raw, box_cfg):
    """Returns (indicator, barcode, container_type) -- barcode is None
    unless the config asks for it to be extracted.

    box_cfg["placeholder"]: true bypasses the raw value entirely and
    always returns a fixed indicator with container_type "folder" (or
    whatever box_cfg["container_type"] says) -- for cases like "map
    case / drawer" that are locations, not discrete countable
    containers, and don't have a reliable per-row identifier to build
    a real container from yet. This is deliberately a stopgap: every
    row using this config shares the SAME placeholder container.
    """
    container_type = box_cfg.get("container_type", "box")

    if box_cfg.get("placeholder"):
        indicator = box_cfg.get("placeholder_indicator", "TBD")
        return indicator, None, box_cfg.get("container_type", "folder")

    if raw is None or str(raw).strip() == "":
        return None, None, container_type
    text = str(raw).strip()

    if not box_cfg.get("compound"):
        try:
            return str(int(float(text))), None, container_type
        except (TypeError, ValueError):
            return text, None, container_type

    # Compound form, e.g. "163: Box 1" -- a leading identifier, a
    # colon, then a label that may or may not itself say "Box N".
    m = re.match(r"^(\d+)\s*:\s*(.+)$", text)
    if not m:
        # Doesn't match the compound shape -- could still be a plain
        # number (e.g. one sheet in a shared config uses bare box
        # numbers while its siblings use "N: Box M"). Try that before
        # falling back to using the raw text as-is.
        try:
            return str(int(float(text))), None, container_type
        except (TypeError, ValueError):
            return text, None, container_type
    leading, label = m.group(1), m.group(2).strip()

    strip_prefix = box_cfg.get("strip_prefix")
    if strip_prefix and label.lower().startswith(strip_prefix.lower()):
        label = label[len(strip_prefix):].strip()

    # Which part is the real, collection-wide container identity?
    # Default is "leading_number" -- in datasets seen so far, the
    # number before the colon climbs steadily and uniquely across
    # sheets/titles (163, 164, 165, ... 1099, 1100 ...), while the
    # part after the colon (often literally "Box 1", "Box 2", ...)
    # repeats identically across every title's own little sub-count
    # and is NOT collection-wide. Using the label as the indicator
    # (the old default) silently merges different physical boxes
    # from different titles that both happen to be "their Box 1".
    indicator_source = box_cfg.get("indicator_source", "leading_number")
    if indicator_source == "leading_number":
        indicator = leading
        barcode = label if box_cfg.get("label_as_barcode") else None
    else:
        indicator = label or text
        barcode = leading if box_cfg.get("barcode_from_prefix") else None
    return indicator, barcode, container_type
