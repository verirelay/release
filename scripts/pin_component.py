#!/usr/bin/env python3
"""Write a component's new commit into versions.lock and clear the affected
digest baselines.

Design constraint (important): automation may only change the lock, never
release.
    - open a PR only; never auto-merge, never trigger a release;
    - when a component changes, the [expected] entries depending on it are
      cleared immediately, so publish mode fails closed until a human runs
      record, reviews the candidates, and publishes manually.

Dependencies (what changes -> which baselines are cleared):
    verirelay -> verirelay_bin_sha256, initrd_sha256   (the initrd embeds the binary)
    veriforge -> initrd_sha256, ovmf_sha256            (the manifest records its commit; ovmf/ may change)

Usage:
    scripts/pin_component.py --lock versions.lock --component verirelay --commit <40-char-sha>
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import NoReturn

DEPENDENTS = {
    "verirelay": ("verirelay_bin_sha256", "initrd_sha256"),
    "veriforge": ("initrd_sha256", "ovmf_sha256"),
}

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


def die(msg: str) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def current_commit(path: str, component: str) -> str | None:
    """Read the component's current commit to decide whether anything actually changed."""
    section = ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1]
                continue
            m = re.match(r'^commit\s*=\s*"([^"]*)"', stripped)
            if section == component and m:
                return m.group(1)
    return None


def rewrite(path: str, component: str, commit: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    section = ""
    changed: list[str] = []
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]

        if section == component and re.match(r"^commit\s*=", stripped):
            out.append(f'commit          = "{commit}"')
            changed.append(f"[{component}].commit = {commit}")
            continue

        if section == "expected":
            # Key names contain digits (*_sha256): using only [a-z_] would silently miss them and the baseline would not be cleared.
            m = re.match(r"^([a-z0-9_]+)\s*=\s*\"[0-9a-f]*\"", stripped)
            if m and m.group(1) in DEPENDENTS[component]:
                key = m.group(1)
                out.append(f'{key} = ""')
                changed.append(f"[expected].{key} cleared (depends on {component})")
                continue

        out.append(line)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", default="versions.lock")
    ap.add_argument("--component", choices=sorted(DEPENDENTS), required=True)
    ap.add_argument("--commit", required=True)
    args = ap.parse_args()

    if not SHA1_RE.match(args.commit):
        die(f"--commit must be a 40-char lowercase hex SHA (got {args.commit!r})")

    # pin is a change action: it must do nothing when the value is unchanged.
    # Otherwise it would clear [expected] baselines for no reason and produce
    # an empty diff, so pin.yml never reaches its "no change" branch.
    if current_commit(args.lock, args.component) == args.commit:
        print(f"[{args.component}] is already {args.commit}; no change")
        return 0

    changed = rewrite(args.lock, args.component, args.commit)
    if not changed:
        die(f"no field for [{args.component}] found in {args.lock}")

    for item in changed:
        print(item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
