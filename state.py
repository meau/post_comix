"""
state.py

A tiny JSON-backed record of what happened to each spreadsheet row on
previous runs, keyed by Excel row number. This is what makes it safe
to: run a --dry-run, then a --limit 5 test, then the full run, then
re-run after fixing an error on row 217 -- without re-creating rows
that already succeeded.

Row status is one of: "success", "error".
Only rows NOT already marked "success" are (re)processed, unless
--force is passed.

Windows note: if this file lives inside a cloud-synced folder
(OneDrive, Dropbox, etc.), the sync client can transiently lock it
while uploading changes -- which collides with state.json being
rewritten after every single row. _save() retries briefly on that
specific failure before giving up, and even then never raises: losing
one row's persisted status is far better than crashing the entire
run (which is what used to happen here, including in the middle of
handling a ROW'S OWN error -- a state-save failure while recording
that a row failed used to crash the whole script rather than just
that row).
"""

import json
import os
import time


class RunState:
    def __init__(self, path: str, log=None, dry_run: bool = False):
        """dry_run: when true, record() becomes a NO-OP -- nothing is
        written to disk, and in-memory self.data isn't touched either.
        A --dry-run preview must never leave a mark that changes what
        a later REAL run does; if it recorded fake "success" entries,
        a subsequent real run would see every row as already done and
        skip it, creating nothing while state.json falsely claims
        everything succeeded. (This is exactly the bug that shipped
        before this fix -- see the fix's own commit/changelog note if
        this file has one.)
        """
        self.path = path
        self.log = log or print
        self.dry_run = dry_run
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {}

    def get(self, row_number: int):
        return self.data.get(str(row_number))

    def was_successful(self, row_number: int) -> bool:
        entry = self.get(row_number)
        return bool(entry and entry.get("status") == "success")

    def record(self, row_number: int, status: str, **fields):
        if self.dry_run:
            return
        entry = {"status": status}
        entry.update(fields)
        self.data[str(row_number)] = entry
        self._save()

    def _save(self):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

        last_exc = None
        for attempt in range(5):
            try:
                os.replace(tmp_path, self.path)
                return
            except PermissionError as exc:
                # Almost always a transient lock from a cloud-sync
                # client (OneDrive, Dropbox, ...) uploading the
                # previous version -- wait briefly and retry.
                last_exc = exc
                time.sleep(0.3 * (attempt + 1))

        # Retries exhausted -- warn, but NEVER raise. This method is
        # called from inside row-processing exception handlers too;
        # letting a save failure propagate there would crash the
        # whole run instead of just costing this one row's persisted
        # status (in-memory self.data still has it for the rest of
        # THIS run -- only a later separate run would lose the
        # distinction and reprocess this row, which is safe, just
        # redundant).
        self.log(
            f"WARNING: could not save {self.path} after 5 attempts ({last_exc}). "
            f"If this file lives in a cloud-synced folder (OneDrive, Dropbox, etc.), "
            f"that's almost certainly why -- the sync client can lock it mid-write. "
            f"Continuing the run; this row's status may not persist to disk if the "
            f"run is interrupted before a later save succeeds."
        )
