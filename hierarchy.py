"""
hierarchy.py

Resolves series/subseries archival_object nodes for spreadsheets that
encode a real institutional-records hierarchy rather than a flat
list -- e.g. Brown's publications sheet, where a "seriesID"/
"seriesTitle" pair can appear on its own (a pure header row), on the
same row as file-level data (that row is BOTH a series boundary and
a file), or be blank (inheriting whatever series was last declared
above it).

Nodes are cached in-memory per run by (resource, parent, level, id),
and idempotency across separate runs is best-effort via a title+
parent-scoped ArchivesSpace search, verified by fetching each
candidate and checking its actual `parent` ref -- the same caution
about search-index field names applies here as it does to top
container / agent reuse elsewhere in this codebase; confirm this
works against your instance with a small --dry-run before trusting
it on a full run.
"""


def _search_existing_node(client, resource_ref, parent_ref, title, level):
    try:
        results = client.search({
            "q": f'"{title}"',
            "type[]": "archival_object",
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
        if record.get("title") != title or record.get("level") != level:
            continue
        record_resource = (record.get("resource") or {}).get("ref")
        record_parent = (record.get("parent") or {}).get("ref")
        if record_resource == resource_ref and record_parent == parent_ref:
            return uri
    return None


def resolve_hierarchy_node(id_value: str, title: str, level: str, parent_ref, resource_ref: str,
                            client, hierarchy_cache: dict, log) -> dict:
    """parent_ref is the URI of the enclosing archival_object (another
    series, for a subseries), or None for a top-level series (parented
    directly on the resource). Returns {"uri": ..., "status": ...}.
    """
    cache_key = (resource_ref, parent_ref, level, id_value or title)
    if cache_key in hierarchy_cache:
        result = dict(hierarchy_cache[cache_key])
        result["status"] = "reused_this_run"
        return result

    existing_uri = _search_existing_node(client, resource_ref, parent_ref, title, level)
    if existing_uri:
        result = {"uri": existing_uri, "status": "reused_existing"}
        log(f'{level.capitalize()} "{title}" ({id_value}): reusing existing node {existing_uri}')
        hierarchy_cache[cache_key] = result
        return result

    payload = {
        "jsonmodel_type": "archival_object",
        "title": title,
        "level": level,
        "publish": False,
        "resource": {"ref": resource_ref},
    }
    if parent_ref:
        payload["parent"] = {"ref": parent_ref}

    repo_prefix = client.repo_prefix
    created = client.post(f"{repo_prefix}/archival_objects", payload)
    uri = created.get("uri")
    result = {"uri": uri, "status": "created"}
    log(f'{level.capitalize()} "{title}" ({id_value}): created {uri}')
    hierarchy_cache[cache_key] = result
    return result


class HierarchyWalker:
    """Tracks "current series", "current subseries", etc. as rows are
    processed in order, per mapping.hierarchy (a list of levels from
    outermost to innermost). Call update() for every row, in order --
    it only actually resolves/creates a node when that level's id/title
    column is non-blank on the row; otherwise the previously-resolved
    node for that level (and its ancestors) is carried forward
    unchanged. Setting a level also clears any deeper levels' state,
    since a new series should not inherit the old subseries.
    """

    def __init__(self, hierarchy_levels, resource_ref, client, log, initial_parent_ref=None):
        """initial_parent_ref: the archival_object a top-level hierarchy
        node should nest under, if the user targeted an archival object
        rather than the resource itself -- None means "directly under
        the resource," which correctly means top-level nodes get NO
        "parent" field at all (only "resource"). Passing resource_ref
        here instead of None would be a real bug: an archival_object's
        "parent" field must reference another archival_object, never
        the resource itself -- ArchivesSpace's schema doesn't allow it.
        """
        self.levels = hierarchy_levels or []
        self.resource_ref = resource_ref
        self.initial_parent_ref = initial_parent_ref
        self.client = client
        self.log = log
        self.cache = {}
        self._current = [None] * len(self.levels)  # each entry: {"uri": ...} or None

    def update(self, row_values, header_index):
        from mapping import get_value
        for depth, level_cfg in enumerate(self.levels):
            id_val = get_value(row_values, header_index, level_cfg.get("id_column"))
            title_val = get_value(row_values, header_index, level_cfg.get("title_column"))
            id_val = str(id_val).strip() if id_val is not None else ""
            title_val = str(title_val).strip() if title_val is not None else ""

            if not id_val and not title_val:
                continue  # inherit whatever this level currently is

            parent_ref = self._current[depth - 1]["uri"] if depth > 0 and self._current[depth - 1] else self.initial_parent_ref
            node = resolve_hierarchy_node(
                id_val or title_val, title_val or id_val, level_cfg["level"],
                parent_ref, self.resource_ref, self.client, self.cache, self.log,
            )
            self._current[depth] = node
            # A new/changed node at this depth invalidates anything deeper.
            for deeper in range(depth + 1, len(self._current)):
                self._current[deeper] = None

    def current_parent_ref(self):
        """The deepest currently-active node's URI, or initial_parent_ref
        (None if the target was the resource itself) if no hierarchy
        level has been set yet."""
        for node in reversed(self._current):
            if node:
                return node["uri"]
        return self.initial_parent_ref
