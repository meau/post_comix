"""
mapping.py

Loads a YAML "mapping config" that tells the generalized migrate.py
how to read a given spreadsheet shape -- which column is the title,
which is the publisher, how to build notes, dates, extents, and box
indicators from it. One config can apply to several sheets that share
a schema (see `sheets:` in the YAML).

A `column` value in a field config can be either:
  - a string  -> looked up by header name (sheet must have a header row)
  - an integer -> a 0-based positional index (works even on headerless
    sheets, or when two columns share the same header text)

See configs/peck_comics.yaml for a fully worked example, and
suggest_mapping.py for a tool that drafts a starting config from a
sheet's actual headers.
"""

import yaml


class MappingConfig:
    def __init__(self, data: dict, path: str = None):
        self.data = data
        self.path = path
        self.name = data.get("name") or path or "mapping"
        self.sheets = data.get("sheets") or []
        self.has_header = data.get("has_header", True)
        self.skip_rows = set(data.get("skip_rows") or [])  # Excel row numbers to skip entirely (e.g. an embedded instructions row)
        self.level = data.get("level", "item")
        self.instance_type = data.get("instance_type", "mixed_materials")
        self.publish_default = data.get("publish_default", False)

        self.title = data.get("title")
        self.publisher = data.get("publisher")
        self.extent = data.get("extent")
        self.date = data.get("date")
        self.physdesc = data.get("physdesc")
        self.box = data.get("box")
        self.ignore_column = data.get("ignore_column")  # {column} -- any non-blank value skips the row entirely
        self.location = data.get("location")  # {building, coordinate_1: {column,label}, coordinate_2:..., coordinate_3:...}
        self.scope_notes = data.get("scope_notes") or []

        # Pass 2 additions:
        self.agents = data.get("agents") or []          # [{column, agent_type: corporate|person, role, relator}]
        self.hierarchy = data.get("hierarchy") or []     # [{level, id_column, title_column}]
        self.genre = data.get("genre")                  # {column}  -- reconciled against Getty AAT
        self.title_or_digital_object = data.get("title_or_digital_object")  # {column, separator}
        self.vocabulary_ref = data.get("vocabulary_ref", "/vocabularies/1")

    @classmethod
    def load(cls, path: str) -> "MappingConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data:
            raise ValueError(f"{path} is empty or not valid YAML.")
        return cls(data, path=path)


def get_value(row_values, header_index: dict, column_ref):
    """Fetch a cell value from a raw row tuple, given either a header
    name (string) or a 0-based positional index (int) in column_ref.
    Returns None if column_ref is None, unmapped, or out of range.
    """
    if column_ref is None:
        return None
    if isinstance(column_ref, int):
        return row_values[column_ref] if column_ref < len(row_values) else None
    if header_index is None:
        return None
    idx = header_index.get(column_ref)
    return row_values[idx] if idx is not None else None
