"""
loc_client.py

Conservative lookups against Library of Congress Linked Data Service
(id.loc.gov) vocabularies -- Name Authority File (people/corporate
bodies), Subject Headings (LCSH), the Thesaurus for Graphic Materials
(TGM), and the RBMS Controlled Vocabulary (RBMSCV). All four are
hosted at id.loc.gov and share the exact same suggest2/label API
shape, just under different base paths -- so this module is
parameterized by base_path rather than hardcoded to names lookups.

We deliberately do NOT do fuzzy/"sounds like" matching. A candidate is
only accepted if it matches the spreadsheet's string once both
strings are normalized for whitespace, casing, and punctuation
(periods, commas). That allows for things like "E.C. Publications" vs.
"E.C Publications" or trailing-period differences, but will NOT match
e.g. "Marvel" to "Marvel Comics" -- that kind of loose match falls
through to local (DACS-described, or otherwise unauthorized) record
creation instead, by design.

Two lookup paths are tried, in order:
  1. The "known label" redirect (`<base_path>/label/<label>`), which
     only resolves when the string we hand it already IS an
     authorized (or a registered variant) heading. This is a stronger
     signal than free-text search, and it's still conservative -- it
     doesn't fuzzy-match anything.
  2. `suggest2`, accepted only on a normalized-exact, unambiguous match.

Network calls retry on timeouts/connection errors and on LC's
retryable HTTP statuses (429/500/502/503/504), with exponential
backoff and jitter -- a rate limit or a dropped connection should
never silently look identical to "this term isn't in the vocabulary."
If every retry is exhausted on *both* lookup paths, LCLookupError is
raised so the caller can tell "we don't know" apart from "confirmed
no match" (see agents.py, people_agents.py, subjects.py).
"""

import random
import re
import time
import unicodedata
from urllib.parse import quote

import requests

BASE_URL = "https://id.loc.gov"
NAMES_PATH = "/authorities/names"       # people, corporate bodies (LC NAF)
SUBJECTS_PATH = "/authorities/subjects"  # LCSH -- also used for FAST-labeled
                                          # and occupation terms, by explicit
                                          # decision: search LCSH and label
                                          # the result "lcsh" rather than
                                          # building separate FAST/LCDGT
                                          # integrations for vocabularies
                                          # with heavy term overlap with LCSH.
TGM_PATH = "/vocabulary/graphicMaterials"
RBMSCV_PATH = "/vocabulary/rbmscv"

# id.loc.gov asks that clients identify themselves.
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PeckComicsArchivesSpaceMigration/1.0 (ArchivesSpace agent lookup script)",
}

RETRIES = 3
BACKOFF_BASE = 1.5
TIMEOUT = 15

MADS_LABEL_KEYS = (
    "http://www.loc.gov/mads/rdf/v1#authoritativeLabel",
    "authoritativeLabel",
    "http://www.w3.org/2004/02/skos/core#prefLabel",
    "prefLabel",
)


class LCLookupError(Exception):
    """Raised when id.loc.gov couldn't be reached/queried reliably after
    retries -- distinct from a confirmed "no match", so callers don't
    mistake a network problem for proof a term isn't in the vocabulary.
    """


def _lc_get(url, params=None, allow_redirects=True, context="LC request"):
    last_exc = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(
                url, params=params, headers=HEADERS,
                timeout=TIMEOUT, allow_redirects=allow_redirects,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{context}: retryable status {resp.status_code}")
            return resp
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_exc = exc
            if attempt == RETRIES:
                break
            time.sleep((BACKOFF_BASE ** attempt) + random.uniform(0, 0.5))
    raise LCLookupError(f"{context} failed after {RETRIES} attempts: {last_exc}")


def normalize(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = text.replace("&", "and")
    text = re.sub(r"[.,]", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _authoritative_label(uri: str):
    """Fetch the real authorized label for an id.loc.gov URI from its
    own record, rather than trusting whatever label text a search
    result handed back. Returns None (not an error) if the record's
    shape is unexpected -- that just means we skip using this URI as
    a match. Works the same for any id.loc.gov vocabulary, not just
    names -- the MADS/SKOS label keys are shared across all of them.
    """
    resp = _lc_get(f"{uri}.json", context="id.loc.gov record fetch")
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None

    graph = data.get("@graph") if isinstance(data, dict) else data
    if not isinstance(graph, list):
        return None

    candidates = {uri, uri.replace("https://", "http://"), uri.replace("http://", "https://")}
    for node in graph:
        if not isinstance(node, dict) or node.get("@id") not in candidates:
            continue
        for key in MADS_LABEL_KEYS:
            val = node.get(key)
            if not val:
                continue
            if isinstance(val, list):
                val = val[0]
            if isinstance(val, dict):
                return val.get("@value") or val.get("value")
            return val
    return None


def _known_label_lookup(name: str, base_path: str):
    """Try id.loc.gov's exact authorized-heading redirect first."""
    url = f"{BASE_URL}{base_path}/label/{quote(name.strip(), safe='')}"
    resp = _lc_get(url, allow_redirects=True, context="id.loc.gov label lookup")
    if base_path not in resp.url:
        return None
    result_uri = resp.url.split("?")[0].split("#")[0]
    if result_uri.endswith((".json", ".rdf", ".madsxml", ".marcxml")):
        result_uri = result_uri.rsplit(".", 1)[0]
    label = _authoritative_label(result_uri)
    if not label:
        return None
    return {"label": label, "uri": result_uri}


def suggest_corporate_names(query: str, count: int = 10) -> list:
    """Raw suggest2 hits for a corporate-name search. Kept as a thin
    wrapper for backward compatibility -- prefer suggest_names().
    """
    return suggest_names(query, base_path=NAMES_PATH, rdftype="CorporateName", count=count)


def suggest_names(query: str, base_path: str = NAMES_PATH, rdftype: str = None, count: int = 10) -> list:
    """Raw suggest2 hits against any id.loc.gov vocabulary at base_path.
    rdftype (e.g. "CorporateName", "PersonalName") only makes sense
    for the names vocabulary -- leave it None for subjects/TGM/RBMSCV.
    """
    params = {"q": query, "count": count}
    if rdftype:
        params["rdftype"] = rdftype
    resp = _lc_get(f"{BASE_URL}{base_path}/suggest2", params=params, context="id.loc.gov suggest")
    try:
        data = resp.json()
    except ValueError:
        return []
    return data.get("hits", [])


def find_conservative_match(name: str, base_path: str = NAMES_PATH, rdftype: str = None):
    """Look for a single, confident match for a name/term against the
    id.loc.gov vocabulary at base_path. rdftype narrows a names lookup
    to "CorporateName" or "PersonalName"; leave it None for subjects/
    TGM/RBMSCV lookups, which don't use it the same way.

    Returns a dict {"label": <authorized label>, "uri": <id.loc.gov URI>}
    if (and only if) a normalized-exact, unambiguous match is found,
    None if we successfully checked and found no such match, or raises
    LCLookupError if id.loc.gov couldn't be reached reliably via either
    lookup path (i.e. we genuinely don't know, rather than "no").
    """
    target = normalize(name)
    if not target:
        return None

    errors = []

    try:
        known = _known_label_lookup(name, base_path)
        if known and normalize(known["label"]) == target:
            return known
    except LCLookupError as exc:
        errors.append(str(exc))

    try:
        hits = suggest_names(name, base_path=base_path, rdftype=rdftype)
    except LCLookupError as exc:
        errors.append(str(exc))
        hits = None

    if hits is not None:
        matches = []
        for hit in hits:
            label = hit.get("aLabel") or hit.get("label") or ""
            uri = hit.get("uri")
            if label and uri and normalize(label) == target:
                matches.append({"label": label, "uri": uri})
        if len(matches) == 1:
            return matches[0]
        # Zero, or more than one, normalized-exact hit -- both are a
        # confirmed "no confident match" (ambiguous == conservative no).
        return None

    # Both lookup paths failed at the network level -- we genuinely
    # don't know, so say so distinctly rather than silently treating
    # it like "not in the vocabulary".
    raise LCLookupError("; ".join(errors))
