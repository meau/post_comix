"""
loc_client.py

Conservative lookups against the LC Linked Data Service (id.loc.gov)
Name Authority File, restricted to corporate-body names (publishers).

We deliberately do NOT do fuzzy/"sounds like" matching. A candidate is
only accepted if it matches the spreadsheet's publisher string once
both strings are normalized for whitespace, casing, and punctuation
(periods, commas). That allows for things like "E.C. Publications" vs.
"E.C Publications" or trailing-period differences, but will NOT match
e.g. "Marvel" to "Marvel Comics" -- that kind of loose match falls
through to local (DACS) agent creation instead, by design.

Two lookup paths are tried, in order:
  1. The "known label" redirect (`/authorities/names/label/<label>`),
     which only resolves when the string we hand it already IS an
     authorized (or a registered variant) heading. This is a stronger
     signal than free-text search, and it's still conservative -- it
     doesn't fuzzy-match anything.
  2. `suggest2`, filtered to corporate names, accepted only on a
     normalized-exact, unambiguous match (as before).

Network calls retry on timeouts/connection errors and on LC's
retryable HTTP statuses (429/500/502/503/504), with exponential
backoff and jitter -- a rate limit or a dropped connection should
never silently look identical to "this publisher isn't in LC NAF."
If every retry is exhausted on *both* lookup paths, LCLookupError is
raised so the caller can tell "we don't know" apart from "confirmed
no match" (see agents.py).
"""

import random
import re
import time
import unicodedata
from urllib.parse import quote

import requests

SUGGEST_URL = "https://id.loc.gov/authorities/names/suggest2"
LABEL_URL = "https://id.loc.gov/authorities/names/label"

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
    mistake a network problem for proof the publisher isn't in LC NAF.
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
    """Fetch the real authorized label for an LC NAF URI from its own
    record, rather than trusting whatever label text a search result
    handed back. Returns None (not an error) if the record's shape is
    unexpected -- that just means we skip using this URI as a match.
    """
    resp = _lc_get(f"{uri}.json", context="LCNAF record fetch")
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


def _known_label_lookup(name: str):
    """Try id.loc.gov's exact authorized-heading redirect first."""
    url = f"{LABEL_URL}/{quote(name.strip(), safe='')}"
    resp = _lc_get(url, allow_redirects=True, context="LCNAF label lookup")
    if "/authorities/names/" not in resp.url:
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
    return suggest_names(query, rdftype="CorporateName", count=count)


def suggest_names(query: str, rdftype: str = "CorporateName", count: int = 10) -> list:
    """Raw suggest2 hits, filtered to a given LC NAF rdftype --
    "CorporateName" or "PersonalName"."""
    params = {"q": query, "count": count, "rdftype": rdftype}
    resp = _lc_get(SUGGEST_URL, params=params, context="LCNAF suggest")
    try:
        data = resp.json()
    except ValueError:
        return []
    return data.get("hits", [])


def find_conservative_match(name: str, rdftype: str = "CorporateName"):
    """Look for a single, confident LC NAF match for a name.
    rdftype is "CorporateName" (default, for publishers/organizations)
    or "PersonalName" (for individual creators).

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
        known = _known_label_lookup(name)
        if known and normalize(known["label"]) == target:
            return known
    except LCLookupError as exc:
        errors.append(str(exc))

    try:
        hits = suggest_names(name, rdftype=rdftype)
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
    # it like "not in LC NAF".
    raise LCLookupError("; ".join(errors))
