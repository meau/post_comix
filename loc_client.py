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
"""

import re
import unicodedata

import requests

SUGGEST_URL = "https://id.loc.gov/authorities/names/suggest2"

# id.loc.gov asks that clients identify themselves.
HEADERS = {
    "User-Agent": "PeckComicsArchivesSpaceMigration/1.0 (ArchivesSpace agent lookup script)"
}


def normalize(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = text.replace("&", "and")
    text = re.sub(r"[.,]", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def suggest_corporate_names(query: str, count: int = 10, timeout: int = 15) -> list:
    """Return the raw list of suggest2 hits for a corporate-name search.
    Returns [] on any network/parse problem rather than raising --
    a lookup failure should never crash the whole migration run.
    """
    params = {"q": query, "count": count, "rdftype": "CorporateName"}
    try:
        resp = requests.get(SUGGEST_URL, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001
        return []

    hits = data.get("hits", [])
    return hits


def find_conservative_match(publisher_name: str):
    """Look for a single, confident LC NAF match for a corporate name.

    Returns a dict {"label": <authorized label>, "uri": <id.loc.gov URI>}
    if (and only if) a normalized-exact match is found, otherwise None.
    """
    target = normalize(publisher_name)
    if not target:
        return None

    hits = suggest_corporate_names(publisher_name)
    matches = []
    for hit in hits:
        label = hit.get("aLabel") or hit.get("label") or ""
        uri = hit.get("uri")
        if not label or not uri:
            continue
        if normalize(label) == target:
            matches.append({"label": label, "uri": uri})

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        # More than one authority record normalizes to the same string --
        # too ambiguous to pick automatically. Conservative == skip.
        return None

    return None
