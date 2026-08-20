#!/usr/bin/env python3
"""Validate / read a roster JSON file — the shell scripts' one dependency.

Both deploy-wd30.sh and authorize-stations.sh must refuse to act on an
empty or malformed roster, and both need the list of station names.
Doing that in bash means parsing JSON with grep, which fails open on
exactly the inputs that matter (an empty array, a `{"error": ...}`
object).  This helper fails CLOSED and is unit-tested, so the scripts
stay a thin wrapper around a tested check.

STDLIB ONLY.  Usable from the repo before deploy and from
/opt/hamsci-fleetboard/ after it.
"""

import argparse
import json
import sys


def read_roster(path):
    """Return the roster list, or raise ValueError explaining the refusal."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"{path}: roster is {type(data).__name__}, "
                         f"expected a JSON array")
    if not data:
        # An empty roster would authorize nobody and watch nothing, while
        # every command still reported success.
        raise ValueError(f"{path}: roster is EMPTY — refusing to proceed")
    names = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: roster entry {entry!r} is not an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}: roster entry {entry!r} has no name")
        names.append(name.strip())
    return names


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check a roster JSON file, or list its station names.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="PATH",
                       help="exit 0 if the roster is usable, 1 otherwise")
    group.add_argument("--names", metavar="PATH",
                       help="print one station name per line (implies --check)")
    args = parser.parse_args(argv)

    path = args.check or args.names
    try:
        names = read_roster(path)
    except ValueError as exc:
        sys.stderr.write(f"roster-check: {exc}\n")
        return 1
    if args.names:
        for name in names:
            print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
