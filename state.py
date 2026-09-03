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
"""

import json
import os


class RunState:
    def __init__(self, path: str):
        self.path = path
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
        entry = {"status": status}
        entry.update(fields)
        self.data[str(row_number)] = entry
        self._save()

    def _save(self):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.path)
