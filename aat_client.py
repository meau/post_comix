"""
aat_client.py

Conservative lookups against the Getty Art & Architecture Thesaurus
(AAT) via Getty's standard Reconciliation Service API
(https://services.getty.edu/vocab/reconcile/), for reconciling a
free-text "typeOfResource"/format value (e.g. "Graphic materials")
against an AAT genre/form term.

Same conservative philosophy as loc_client.py: no fuzzy/"sounds like"
matching. A candidate is accepted only if Getty's own service marks
it a strong match ("match": true) or if exactly one candidate's name
normalizes identically to our input -- ambiguous or absent results
both come back as "no confident match" rather than a guess.
"""

import random
import re
import time
import unicodedata

import requests

RECONCILE_URL = "https://services.getty.edu/vocab/reconcile/"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "ArchivesSpaceMigration/1.0 (Getty AAT genre-term lookup script)",
}

RETRIES = 3
BACKOFF_BASE = 1.5
TIMEOUT = 15


class AATLookupError(Exception):
    """Raised when Getty's reconciliation service couldn't be reached
    reliably after retries -- distinct from a confirmed "no match",
    same reasoning as LCLookupError in loc_client.py.
    """


def _post(params, context="AAT reconcile"):
    last_exc = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.post(RECONCILE_URL, data=params, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{context}: retryable status {resp.status_code}")
            return resp
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_exc = exc
            if attempt == RETRIES:
                break
            time.sleep((BACKOFF_BASE ** attempt) + random.uniform(0, 0.5))
    raise AATLookupError(f"{context} failed after {RETRIES} attempts: {last_exc}")


def normalize(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = re.sub(r"[.,]", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def find_conservative_match(term_name: str):
    """Returns {"label": <preferred term>, "uri": <vocab.getty.edu URI>}
    on a confident match, None on a confirmed absence, or raises
    AATLookupError if the service couldn't be reached.
    """
    target = normalize(term_name)
    if not target:
        return None

    import json as _json
    query = {"q0": {"query": term_name, "type": "/aat", "limit": 10}}
    resp = _post({"queries": _json.dumps(query)}, context="AAT reconcile")
    try:
        data = resp.json()
    except ValueError:
        return None

    results = (data.get("q0") or {}).get("result", [])

    # 1. Getty's own service marked one a strong match.
    strong = [r for r in results if r.get("match")]
    if len(strong) == 1:
        return _to_match(strong[0])
    if len(strong) > 1:
        return None  # ambiguous -- conservative no

    # 2. Otherwise, accept only a single normalized-exact name match.
    exact = [r for r in results if normalize(r.get("name", "")) == target]
    if len(exact) == 1:
        return _to_match(exact[0])

    return None


def _to_match(result: dict):
    aat_id = result.get("id", "")  # e.g. "aat/300198841"
    aat_id = aat_id.split("/")[-1]
    return {
        "label": result.get("name"),
        "uri": f"http://vocab.getty.edu/aat/{aat_id}",
    }
