#!/usr/bin/env python3
"""
list_enumerations.py

Prints the ACTUAL controlled value lists (enumerations) from your own
ArchivesSpace instance -- extent types, date labels, agent roles,
note types, and so on. This is the authoritative source for "what
values am I allowed to put in this config field," not any static doc
(including docs/ENUMERATIONS.md in this repo) -- ArchivesSpace lets
each institution customize these lists via "Manage Controlled Value
Lists" in the staff UI, so what's valid on YOUR instance can differ
from the stock defaults.

Usage:
  python list_enumerations.py                     # everything
  python list_enumerations.py --grep extent        # just enumerations whose name contains "extent"
  python list_enumerations.py --grep date_label
  python list_enumerations.py --grep relator

This only ever reads (GET /config/enumerations) -- it works the same
whether or not your API account has write access, and works with
--dry-run's read-only behavior for free (there's nothing to write).
"""

import argparse

from aspace_client import ArchivesSpaceClient, ArchivesSpaceError
from migrate import load_or_init_secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--secrets", default="secrets.json")
    parser.add_argument("--grep", default=None,
                         help="Only show enumerations whose name contains this substring (case-insensitive).")
    args = parser.parse_args()

    secrets = load_or_init_secrets(args.secrets)

    def log(msg):
        pass  # this script's own prints are the output; no need to echo the client's internal log too

    client = ArchivesSpaceClient(
        api_url=secrets["api_url"], username=secrets["username"], password=secrets["password"],
        repository_id=secrets["repository_id"], dry_run=False, log=log,
    )

    try:
        enumerations = client.get("/config/enumerations")
    except ArchivesSpaceError as exc:
        print(f"Could not fetch enumerations: {exc}")
        return

    if not enumerations:
        print("No enumerations returned -- check your connection/credentials.")
        return

    if args.grep:
        needle = args.grep.lower()
        enumerations = [e for e in enumerations if needle in e.get("name", "").lower()]
        if not enumerations:
            print(f"No enumeration names contain {args.grep!r}. Try a shorter substring, "
                  f"or run with no --grep to see every enumeration name.")
            return

    for enum in sorted(enumerations, key=lambda e: e.get("name", "")):
        name = enum.get("name", "(unnamed)")
        values = enum.get("values", [])
        editable = enum.get("editable", True)
        print(f"\n{name}" + ("" if editable else "  [not editable on this instance]"))
        for v in values:
            print(f"  - {v}")


if __name__ == "__main__":
    main()
