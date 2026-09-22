"""
aspace_client.py

Minimal wrapper around the ArchivesSpace "backend" REST API
(https://archivesspace.github.io/archivesspace/api/).

In --dry-run mode, GET/search calls still hit the real API (so you get
a realistic preview of what already exists -- e.g. which agents/top
containers would be reused vs. created), but POST/PUT calls are
intercepted, logged, and answered with a fake placeholder URI so the
rest of the script can keep running as if the create had succeeded.

Brown-specific note (from JHL-ArchivesSpace_API-Instructions_notes.pdf):
even while on the Brown VPN, IPv6 traffic to the API gets blocked by
Atlas' Cloudflare, and Python's default DNS resolution can pick IPv6
first. This module forces IPv4 resolution for all requests made
through it, per that document's own recommended fix, so this doesn't
surface as a confusing connection failure on first run.
"""

import itertools
import socket
import sys

import requests

# === FORCE IPv4 TRAFFIC (BLOCK IPv6) ===
# Per Brown's own API notes: required even on VPN, or requests can
# silently hang/fail against Atlas' Cloudflare-fronted API.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo


class ArchivesSpaceError(Exception):
    pass


class ArchivesSpaceClient:
    def __init__(self, api_url: str, username: str, password: str,
                 repository_id: str, dry_run: bool = False, log=None):
        self.base_url = api_url.rstrip("/")
        self.repository_id = str(repository_id)
        self.dry_run = dry_run
        self.log = log or (lambda *a, **k: None)
        self.session = requests.Session()
        self._token = None
        self._fake_id_counter = itertools.count(1)

        if not dry_run:
            self._login(username, password)
        else:
            # Dry runs still want to READ real data (to preview matches),
            # so we still log in if credentials were supplied.
            if username and password:
                try:
                    self._login(username, password)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"[dry-run] Could not log in for read-only "
                              f"preview ({exc}); continuing fully offline.")

    def _login(self, username: str, password: str):
        url = f"{self.base_url}/users/{username}/login"
        resp = self.session.post(url, params={"password": password}, timeout=30)
        if resp.status_code != 200:
            raise ArchivesSpaceError(
                f"Login failed ({resp.status_code}): {resp.text[:500]}"
            )
        self._token = resp.json().get("session")
        if not self._token:
            raise ArchivesSpaceError(f"Login response had no session token: {resp.text[:500]}")
        self.session.headers.update({"X-ArchivesSpace-Session": self._token})

    @property
    def repo_prefix(self) -> str:
        return f"/repositories/{self.repository_id}"

    def get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params or {}, timeout=30)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise ArchivesSpaceError(
                f"GET {path} failed ({resp.status_code}): {resp.text[:500]}"
            )
        return resp.json()

    def post(self, path: str, json_body: dict) -> dict:
        """POST (create/update). Honors dry_run."""
        if self.dry_run:
            fake_id = next(self._fake_id_counter)
            fake_uri = f"{path}/DRY-RUN-{fake_id}"
            self.log(f"[dry-run] Would POST {path}:\n{_pretty(json_body)}")
            return {"status": "created (dry-run)", "uri": fake_uri, "id": f"DRY-RUN-{fake_id}"}

        url = f"{self.base_url}{path}"
        resp = self.session.post(url, json=json_body, timeout=30)
        if resp.status_code not in (200, 201):
            raise ArchivesSpaceError(
                f"POST {path} failed ({resp.status_code}): {resp.text[:800]}"
            )
        return resp.json()

    def search(self, params: dict) -> dict:
        """Read-only -- always hits the real API, even in dry-run,
        so long as we successfully logged in."""
        return self.get("/search", params=params) or {"results": []}


def _pretty(obj) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)


def parse_conflicting_record_uri(error_text: str, uri_pattern: str = None):
    """Extracts the conflicting record's URI from an ArchivesSpace
    "must be unique" style 400 error, e.g.

      {"error":{"names":["Agent must be unique"],
                "conflicting_record":["/agents/corporate_entities/7687"]}}

    ArchivesSpaceError's message has a prefix before the JSON body
    ("POST /x failed (400): {...}"), so this finds the embedded JSON
    object rather than requiring the whole string to parse as JSON.
    uri_pattern (a regex) is a fallback for when the JSON shape isn't
    what's expected; pass the record-type-specific URI pattern for
    the kind of record you're creating (e.g. r"/agents/people/\\d+").

    This exists because a search-then-create pattern (used throughout
    this codebase for every record type it creates-if-missing) is
    vulnerable to ArchivesSpace's search index lagging behind recent
    writes: a record created moments ago on an earlier row may not be
    findable yet by a search on a later row, even though it genuinely
    exists -- leading to a duplicate-create attempt that the database's
    own uniqueness constraint correctly rejects. Catching that
    rejection and reusing the conflicting record it names is the fix;
    letting it propagate and crash that row is the bug.
    """
    import json as _json
    import re

    brace_idx = error_text.find("{")
    if brace_idx != -1:
        try:
            data = _json.loads(error_text[brace_idx:])
            conflicting = data.get("error", {}).get("conflicting_record")
            if conflicting:
                return conflicting[0]
        except Exception:  # noqa: BLE001
            pass

    if uri_pattern:
        match = re.search(uri_pattern, error_text)
        if match:
            return match.group(0)

    return None
