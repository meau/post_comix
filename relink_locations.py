#!/usr/bin/env python3
"""
relink_locations.py

Second half of the location workflow (see missing_locations.py and
locations.py). Run this after staff have created the Location
records listed in a migrate.py run's "missing locations" report.

For every entry in the pending-relink file, re-searches ArchivesSpace
for a matching Location (now hopefully created) and, if found,
attaches it to the already-existing top_container via
container_locations -- fetching the container fresh immediately
before updating (so we're not clobbering a concurrent edit) and using
its current lock_version, per ArchivesSpace's optimistic-concurrency
update convention.

Entries still unresolved after a run stay in the pending file, so
this is safe to re-run repeatedly as more locations get created over
time -- each run only ever narrows the file, never duplicates it.

Usage:
  python relink_locations.py --pending-relink-file pending_relink.json --dry-run
  python relink_locations.py --pending-relink-file pending_relink.json
"""

import argparse
import json
import sys

from aspace_client import ArchivesSpaceClient, ArchivesSpaceError
from locations import resolve_location, build_container_locations
from missing_locations import coordinates_from_jsonable
from migrate import load_or_init_secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pending-relink-file", default="pending_relink.json")
    parser.add_argument("--secrets", default="secrets.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    secrets = load_or_init_secrets(args.secrets)

    def log(msg):
        print(msg)

    client = ArchivesSpaceClient(
        api_url=secrets["api_url"], username=secrets["username"], password=secrets["password"],
        repository_id=secrets["repository_id"], dry_run=args.dry_run, log=log,
    )

    try:
        with open(args.pending_relink_file, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except FileNotFoundError:
        print(f"{args.pending_relink_file} not found -- nothing to relink.")
        sys.exit(0)

    if not pending:
        print(f"{args.pending_relink_file} is empty -- nothing to relink.")
        return

    location_cache = {}
    still_pending = []
    relinked, still_missing, errored = 0, 0, 0

    for entry in pending:
        uri = entry["top_container_uri"]
        building = entry.get("building")
        coords = coordinates_from_jsonable(entry["coordinates"])

        location_link = resolve_location(coords, building, client, location_cache, log)
        if not location_link:
            still_missing += 1
            still_pending.append(entry)
            continue

        try:
            record = client.get(uri)
            if not record:
                log(f"{uri}: not found (deleted?) -- dropping from pending list.")
                continue

            existing_locations = record.get("container_locations") or []
            already_linked = any(loc.get("ref") == location_link["uri"] for loc in existing_locations)
            if not already_linked:
                record["container_locations"] = existing_locations + build_container_locations(location_link)

            client.post(uri, record)
            log(f"{uri}: linked to location {location_link['uri']}")
            relinked += 1

        except ArchivesSpaceError as exc:
            log(f"{uri}: ERROR -- {exc}")
            still_pending.append(entry)
            errored += 1

    if not args.dry_run:
        with open(args.pending_relink_file, "w", encoding="utf-8") as f:
            json.dump(still_pending, f, indent=2)

    print()
    print("=== Summary ===")
    print(f"Relinked: {relinked}")
    print(f"Still missing (location not yet created): {still_missing}")
    print(f"Errored: {errored}")
    if args.dry_run:
        print(f"[dry-run] {args.pending_relink_file} was not modified.")
    else:
        print(f"{args.pending_relink_file} now has {len(still_pending)} entr{'y' if len(still_pending)==1 else 'ies'} remaining.")


if __name__ == "__main__":
    main()
