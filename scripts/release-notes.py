#!/usr/bin/env python3
"""Generate the release notes (Markdown).

Why this script exists
----------------------
The release page is the one place users actually read.
`gh release create --generate-notes` only assembles PR titles, which says
almost nothing for a release of four byte streams plus measurements. Instead
the notes are generated from chain.json: sources, Launch Measurements with
their recompute command, artifact digests, the verification command, and the
diff against the previous release.

All content comes from chain.json and versions.lock; nothing is written or
copied by hand, so the text cannot drift from the facts.

Usage
----
    python3 scripts/release-notes.py render --chain chain.json \\
        --previous prev-chain.json --out RELEASE_NOTES.md
    python3 scripts/release-notes.py build-records --out RELEASE_NOTES.md

`--previous` is optional: given the previous release's chain.json it adds a
"changes" section. `build-records` appends the Build Records section after
publish mode has registered them in Registry.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from typing import NoReturn

ARTIFACT_ORDER = [
    "initrd.img",
    "vmlinuz",
    "OVMF.fd",
    "verirelay-linux-amd64",
    "build-report.json",
    "artifacts.lock",
    "SHA256SUMS",
]

COMPONENT_LABELS = {
    "verirelay": "verirelay",
    "veriforge": "veriforge",
    "ovmf": "edk2 (OVMF)",
}

WORKFLOW = "release.yml"
WORKFLOW_REF = "refs/heads/main"


def die(msg: str) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    v = n / 1024
    for unit in ("KiB", "MiB"):
        if v < 1024:
            return f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} GiB"


def component_commit(entry: dict) -> str:
    return entry.get("commit") or entry.get("edk2_commit") or "?"


def release_title(chain: dict) -> str:
    return chain["release"].get("tag") or "Record build"


def section_sources(chain: dict) -> str:
    rel = chain["release"]
    comps = chain["components"]
    lines = [
        "The Boot Artifact Set of a VeriRelay CVM, built from these sources.",
        "",
        "| | |",
        "| --- | --- |",
    ]
    lines += [
        f"| {label} | `{component_commit(comps[name])}` |"
        for name, label in COMPONENT_LABELS.items()
    ]
    lines.append(f"| `versions.lock` | `{rel['lock_sha256']}` |")
    if rel.get("run_url"):
        lines.append(f"| Build run | {rel['run_url']} |")
    return "\n".join(lines)


def vcpu_label(vcpus: int) -> str:
    return f"{vcpus} vCPU" if vcpus == 1 else f"{vcpus} vCPUs"


def section_measurement(chain: dict) -> str:
    entries = chain.get("launch_measurements")
    if not entries:
        return "## Launch Measurements\n\nThis release records no Launch Measurements."
    lines = [
        "## Launch Measurements",
        "",
        (
            "A CVM that boots these artifacts with the parameters in the command "
            "below shows the Launch Measurement for its vCPU count in its "
            "Attestation Report."
        ),
        "",
        "| vCPUs | Launch Measurement |",
        "| --- | --- |",
    ]
    lines += [f"| {e['vcpus']} | `{e['measurement']}` |" for e in entries]
    lines += ["", "Recompute them from the downloaded artifacts:", ""]
    lines += _measurement_recompute_lines(entries)
    return "\n".join(lines)


def _measurement_recompute_lines(entries: list[dict]) -> list[str]:
    # One loop covers every vCPU count, which holds only while the entries
    # differ in nothing else.
    first = entries[0]
    if any(
        e["params"] != first["params"] or e["tool"] != first["tool"] for e in entries
    ):
        die(
            "launch_measurements differ in params or tool; one recompute loop cannot cover them"
        )
    p = first["params"]
    vcpus = " ".join(str(e["vcpus"]) for e in entries)
    return [
        "```bash",
        f"for n in {vcpus}; do",
        # The tool version must be pinned: the measurement is a function of
        # inputs and tool version. Without a pin, the next tool release would
        # silently change the recompute command on every old release page, and
        # users would read "launch chain mismatch" when only the tool changed.
        f"  uvx --from sev-snp-measure=={first['tool']['version']} sev-snp-measure \\",
        f'    --mode {p["mode"]} --vmm-type {p["vmm_type"]} --vcpus "$n" --vcpu-type {p["vcpu_type"]} --guest-features {p["guest_features"]} \\',
        "    --ovmf OVMF.fd --kernel vmlinuz --initrd initrd.img \\",
        f"    --append {shlex.quote(p['kernel_append'])} --output-format hex",
        "done",
        "```",
    ]


def section_artifacts(chain: dict) -> str:
    arts = chain["artifacts"]
    keys = [k for k in ARTIFACT_ORDER if k in arts] + [
        k for k in sorted(arts) if k not in ARTIFACT_ORDER
    ]
    lines = ["## Artifacts", "", "| File | SHA-256 | Size |", "| --- | --- | --- |"]
    for k in keys:
        v = arts[k]
        lines.append(f"| `{k}` | `{v['sha256']}` | {fmt_bytes(v['size'])} |")
    return "\n".join(lines)


def section_verify(repo: str, mode: str) -> str:
    if mode != "publish":
        return "## Verify\n\n> Record build: built and recorded, not signed."
    # Two things must be right:
    #   1) dots in the literal must be escaped (Python source needs \\ to emit one \);
    #   2) the interpolated repo / workflow must be escaped too — the "." in
    #      "release.yml" is a regex wildcard, and unescaped it would loosen the
    #      identity assertion (release-yml@ would match).
    identity = (
        "^https://github\\.com/"
        + re.escape(repo)
        + "/\\.github/workflows/"
        + re.escape(WORKFLOW)
        + "@"
        + re.escape(WORKFLOW_REF)
        + "$"
    )
    return "\n".join(
        [
            "## Verify",
            "",
            "Each file comes with a `.cosign.bundle` from this repository's release workflow on `main`:",
            "",
            "```bash",
            "cosign verify-blob --bundle <file>.cosign.bundle \\",
            f"  --certificate-identity-regexp '{identity}' \\",
            "  --certificate-oidc-issuer https://token.actions.githubusercontent.com \\",
            "  <file>",
            "```",
            "",
            "`chain.json` is the Release Evidence Chain: it links each Launch Measurement to the sources above.",
        ]
    )


def section_build_records() -> str:
    return (
        "## Build Records\n\n"
        "`build-records.json` holds one Build Record per Launch Measurement, "
        "signed with the Release Key and registered in Registry. Verifier "
        "uses them to match an Attestation Report to this release."
    )


def section_changes(chain: dict, prev: dict | None) -> str:
    if not prev:
        return ""
    previous = prev["release"].get("tag") or "previous release"
    lines = [f"## Changes since `{previous}`", ""]
    lines += _component_changes(chain, prev) or ["- Source commits unchanged"]
    lines += _measurement_changes(chain, prev)
    lines += ["", "| Artifact | Change |", "| --- | --- |"]
    _append_artifact_changes(lines, chain, prev)
    return "\n".join(lines)


def _component_changes(chain: dict, prev: dict) -> list[str]:
    changes = []
    for name, label in COMPONENT_LABELS.items():
        a = component_commit(prev["components"].get(name, {}))
        b = component_commit(chain["components"].get(name, {}))
        if a != b:
            changes.append(f"- {label}: `{a[:12]}` -> `{b[:12]}`")
    return changes


def _append_artifact_changes(lines: list[str], chain: dict, prev: dict) -> None:
    for k in sorted(set(prev["artifacts"]) | set(chain["artifacts"])):
        a = prev["artifacts"].get(k, {}).get("sha256")
        b = chain["artifacts"].get(k, {}).get("sha256")
        if a == b:
            lines.append(f"| `{k}` | unchanged |")
        elif a and b:
            lines.append(f"| `{k}` | **changed** `{a[:12]}...` -> `{b[:12]}...` |")
        elif b:
            lines.append(f"| `{k}` | added |")
        else:
            lines.append(f"| `{k}` | removed |")


def _measurements_by_vcpus(chain: dict) -> dict[int, str]:
    return {
        e["vcpus"]: e["measurement"] for e in chain.get("launch_measurements") or []
    }


def _measurement_changes(chain: dict, prev: dict) -> list[str]:
    before = _measurements_by_vcpus(prev)
    after = _measurements_by_vcpus(chain)
    lines = []
    for vcpus in sorted(set(before) | set(after)):
        pm, cm = before.get(vcpus), after.get(vcpus)
        if pm == cm:
            note = "unchanged"
        elif pm is None:
            note = "added"
        elif cm is None:
            note = "removed"
        else:
            note = f"**changed** `{pm[:12]}...` -> `{cm[:12]}...`"
        lines.append(f"- Launch Measurement, {vcpu_label(vcpus)}: {note}")
    return lines


def _read_previous_chain(path: str | None) -> dict | None:
    prev = None
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                prev = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(
                f"warning: cannot read previous chain.json ({e}); skipping the diff section",
                file=sys.stderr,
            )
        else:
            missing = [
                k for k in ("release", "components", "artifacts") if k not in prev
            ]
            if missing:
                print(
                    f"warning: previous chain.json is missing {missing}; skipping the diff section",
                    file=sys.stderr,
                )
                prev = None
    return prev


def _render_release_notes(
    chain: dict, args: argparse.Namespace, prev: dict | None
) -> str:
    sections = [
        f"# {release_title(chain)}",
        section_sources(chain),
        section_changes(chain, prev),
        section_measurement(chain),
        section_artifacts(chain),
        section_verify(args.repo, chain["release"]["mode"]),
    ]
    return "\n\n".join(s for s in sections if s) + "\n"


def render(args: argparse.Namespace) -> None:
    with open(args.chain, encoding="utf-8") as f:
        chain = json.load(f)
    prev = _read_previous_chain(args.previous)
    body = _render_release_notes(chain, args, prev)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"release notes written: {args.out} ({len(body)} bytes)", file=sys.stderr)


def build_records(args: argparse.Namespace) -> None:
    with open(args.out, "a", encoding="utf-8") as f:
        f.write("\n" + section_build_records() + "\n")
    print(f"Build Records section appended: {args.out}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(required=True)

    rp = sub.add_parser("render", help="write the release notes")
    rp.add_argument("--chain", required=True, help="chain.json produced by this build")
    rp.add_argument(
        "--previous", default=None, help="chain.json of the previous release"
    )
    rp.add_argument("--out", required=True)
    rp.add_argument("--repo", default="verirelay/release")
    rp.set_defaults(func=render)

    bp = sub.add_parser(
        "build-records", help="append the Build Records section to existing notes"
    )
    bp.add_argument("--out", required=True, help="release notes to append to")
    bp.set_defaults(func=build_records)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
