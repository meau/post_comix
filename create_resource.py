#!/usr/bin/env python3
"""
create_resource.py

Reads a "Collection-Level Data" sheet and creates the corresponding
ArchivesSpace resource record IF one doesn't already exist (matched
by callNumber/id_0, falling back to an exact title match if
callNumber is blank -- a weaker check, see the warning it prints).
Never updates an existing resource -- if one is found, this just
prints its URI so you can use it with migrate.py.

Usage:
  python create_resource.py --xlsx Topic_Files.xlsx --dry-run
  python create_resource.py --xlsx Topic_Files.xlsx --sheet "Collection-Level Data"

On success, prints the resource's URI -- paste that (or its staff/
public URL) into migrate.py's resource prompt to migrate that
collection's rows into it.
"""

import argparse
import json
import sys

from aspace_client import ArchivesSpaceClient, ArchivesSpaceError
from agents import resolve_publisher_agent
from people_agents import resolve_person_agent
from migrate import load_or_init_secrets
from resource_builder import read_collection_level_data, check_required_fields, build_resource_payload


def _search_existing_resource(client, id_0: str, title: str):
    try:
        if id_0:
            results = client.search({"q": f'"{id_0}"', "type[]": "resource", "page": 1})
            for hit in results.get("results", []):
                json_data = hit.get("json")
                if json_data and json.loads(json_data).get("id_0") == id_0:
                    return hit.get("uri")
        results = client.search({"q": f'"{title}"', "type[]": "resource", "page": 1})
        for hit in results.get("results", []):
            if hit.get("title") == title:
                return hit.get("uri")
    except Exception:  # noqa: BLE001
        return None
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--sheet", default="Collection-Level Data")
    parser.add_argument("--secrets", default="secrets.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    secrets = load_or_init_secrets(args.secrets)
    fields = read_collection_level_data(args.xlsx, args.sheet)

    warnings = []
    check_required_fields(fields, warnings)

    def log(msg):
        print(msg)

    client = ArchivesSpaceClient(
        api_url=secrets["api_url"], username=secrets["username"], password=secrets["password"],
        repository_id=secrets["repository_id"], dry_run=args.dry_run, log=log,
    )

    existing_uri = _search_existing_resource(client, fields.get("callNumber", ""), fields.get("title", ""))
    if existing_uri:
        print(f"A resource matching this collection already exists: {existing_uri}")
        print("Not creating a duplicate. Use this URI with migrate.py.")
        return

    if not fields.get("callNumber"):
        print("WARNING: callNumber is blank, so existence was only checked by exact title match "
              "(weaker than an ID match) -- if this creates a duplicate on a re-run, that's why.")

    agent_cache = {}

    def resolve_creator_agent(name, agent_type):
        resolver = resolve_person_agent if agent_type == "person" else resolve_publisher_agent
        return resolver(name, client, agent_cache, log)

    payload = build_resource_payload(fields, resolve_creator_agent, warnings)

    if warnings:
        print("\n=== Warnings ===")
        for w in warnings:
            print(f"  - {w}")
        print()

    missing_required = [w for w in warnings if "very likely be rejected" in w or "primary identifier" in w]
    if missing_required and not args.dry_run:
        print("Refusing to create: required fields are missing (see warnings above). "
              "Fill in Collection-Level Data, or run with --dry-run to preview anyway.")
        sys.exit(1)

    print("=== Resource payload ===")
    print(json.dumps(payload, indent=2))

    if args.dry_run:
        print("\n[dry-run] Not creating -- rerun without --dry-run once the warnings above are resolved.")
        return

    try:
        result = client.post(f"{client.repo_prefix}/resources", payload)
    except ArchivesSpaceError as exc:
        print(f"Resource creation failed: {exc}")
        sys.exit(1)

    print(f"\nCreated resource: {result.get('uri')}")
    print("Use this URI with migrate.py to add this collection's rows to it.")


if __name__ == "__main__":
    main()
