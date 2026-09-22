"""
persistent_cache.py

A small JSON-backed cache that survives across separate script runs
-- used wherever ArchivesSpace's search-based "does this already
exist" check has proven unreliable, so re-running the same migration
doesn't recreate records the SAME script already created earlier.

This exists because of a real, confirmed failure: top_container
lookup (containers.py) relies on a search-index field
(collection_uri_u_sstr) to confirm a candidate belongs to the right
resource, and that field isn't guaranteed to match across
ArchivesSpace instances/versions -- when it doesn't, every search
comes back empty and the fallback (create a new one, since creating a
duplicate is safer than risking a wrong reuse) means EVERY re-run
recreates every container from scratch. A cache we control ourselves
sidesteps the unreliable search entirely for the common case: running
the same script against the same target more than once.

Cache keys are tuples (e.g. (resource_ref, container_type,
indicator)) -- JSON only allows string keys, so each tuple is
serialized as a JSON list string for storage and parsed back on load.

Save uses the same retry-then-warn-don't-crash behavior as state.py,
for the same reason: these files can live in a cloud-synced folder
that transiently locks them mid-write.
"""

import json
import os
import time


def load_cache(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {tuple(json.loads(k)): v for k, v in raw.items()}


def save_cache(path: str, data: dict, log=None):
    log = log or print
    raw = {json.dumps(list(k), ensure_ascii=False): v for k, v in data.items()}

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)

    last_exc = None
    for attempt in range(5):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(0.3 * (attempt + 1))

    log(f"WARNING: could not save {path} after 5 attempts ({last_exc}). "
        f"If this file lives in a cloud-synced folder (OneDrive, Dropbox, etc.), "
        f"that's almost certainly why. Continuing the run; this cache update may "
        f"not persist to disk if the run is interrupted before a later save succeeds.")
