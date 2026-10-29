#!/usr/bin/env python3
"""Generate the release notes (Markdown).

Why this script exists
----------------------
The release page is the one place users actually read.
`gh release create --generate-notes` only assembles PR titles, which says
almost nothing for a release of four byte streams plus measurements. Instead
the notes are generated from chain.json: identity, one launch measurement and
recompute command per vCPU count, artifact digests, verification commands,
and the diff against the previous release.

All content comes from chain.json and versions.lock; nothing is written or
copied by hand, so the text cannot drift from the facts.

Usage
----
    python3 scripts/release-notes.py --chain chain.json \\
        --previous prev-chain.json --out RELEASE_NOTES.md

`--previous` is optional: given the previous release's chain.json it adds a "what changed" section.
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

COMPONENT_ORDER = ["verirelay", "veriforge", "ovmf"]

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


def section_identity(chain: dict) -> str:
    rel = chain["release"]
    comps = chain["components"]
    lines = [
        "## Identity",
        "",
        "| | |",
        "| --- | --- |",
        f"| release | `{rel.get('tag', 'Record build')}` ({rel['mode']} mode) |",
        f"| verirelay | `{component_commit(comps['verirelay'])}` |",
        f"| veriforge | `{component_commit(comps['veriforge'])}` |",
        f"| ovmf | `{component_commit(comps['ovmf'])}` |",
        f"| versions.lock | `{rel['lock_sha256']}` |",
    ]
    if rel.get("run_url"):
        lines.append(f"| build run | {rel['run_url']} |")
    return "\n".join(lines)


def vcpu_label(vcpus: int) -> str:
    return f"{vcpus} vCPU" if vcpus == 1 else f"{vcpus} vCPUs"


def section_measurement(chain: dict) -> str:
    entries = chain.get("launch_measurements")
    if not entries:
        return (
            "## Launch measurements (SEV-SNP)\n\n"
            "This release does not record launch measurements.\n"
        )
    lines = [
        "## Launch measurements (SEV-SNP)",
        "",
        (
            "One Boot Artifact Set serves every vCPU count, and the measurement "
            "depends on the vCPU count. Each value is 48 bytes and equals the "
            "`measurement` field the firmware writes into the attestation report at "
            "guest boot, provided the actual launch configuration matches its "
            "parameters exactly."
        ),
        "",
        (
            "> These are **expected values computed offline**, not authoritative "
            "ones. The authoritative value can only come from a real report. Compare "
            "the `measurement` in a real report with the value for its vCPU count: a "
            "mismatch means something in the launch chain was not recorded (VMM "
            "version, machine type, EFI variables, extra ACPI tables, ...)."
        ),
        "",
        "| vCPUs | measurement |",
        "| --- | --- |",
    ]
    lines += [f"| {e['vcpus']} | `{e['measurement']}` |" for e in entries]
    lines += [
        "",
        "Recompute yourself (using this release's artifacts; file names match the downloaded names):",
        "",
    ]
    for entry in entries:
        lines += _measurement_recompute_lines(entry)
    return "\n".join(lines)


def _measurement_recompute_lines(entry: dict) -> list[str]:
    p = entry["params"]
    return [
        f"### {vcpu_label(entry['vcpus'])}",
        "",
        "```bash",
        # The tool version must be pinned: the measurement is a function of
        # inputs and tool version. Without a pin, the next tool release would
        # silently change the recompute command on every old release page, and
        # users would read "launch chain mismatch" when only the tool changed.
        f"uvx --from sev-snp-measure=={entry['tool']['version']} sev-snp-measure \\",
        f"  --mode {p['mode']} --vmm-type {p['vmm_type']} \\",
        f"  --vcpus {entry['vcpus']} --vcpu-type {p['vcpu_type']} --guest-features {p['guest_features']} \\",
        "  --ovmf OVMF.fd --kernel vmlinuz --initrd initrd.img \\",
        f"  --append {shlex.quote(p['kernel_append'])} \\",
        "  --output-format hex",
        "```",
        "",
    ]


def section_artifacts(chain: dict) -> str:
    arts = chain["artifacts"]
    keys = [k for k in ARTIFACT_ORDER if k in arts] + [
        k for k in sorted(arts) if k not in ARTIFACT_ORDER
    ]
    lines = ["## Artifacts", "", "| file | sha256 | size |", "| --- | --- | --- |"]
    for k in keys:
        v = arts[k]
        lines.append(f"| `{k}` | `{v['sha256']}` | {fmt_bytes(v['size'])} |")
    return "\n".join(lines)


def section_verify(repo: str, mode: str) -> str:
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
    if mode != "publish":
        return "\n".join(
            [
                "## Verify the source",
                "",
                (
                    f"> This is **{mode} mode**: build and record only; nothing is "
                    "signed or published. In publish mode every artifact ships a "
                    "`.cosign.bundle`."
                ),
            ]
        )
    return "\n".join(
        [
            "## Verify the source (no credentials needed)",
            "",
            (
                "Every artifact ships a `.cosign.bundle` packaging the signature, the "
                "short-lived Fulcio certificate, and the Rekor transparency-log entry. "
                "Verifying it confirms the file was really built by this repository's "
                "release pipeline."
            ),
            "",
            "```bash",
            "cosign verify-blob --bundle <file>.cosign.bundle \\",
            f"  --certificate-identity-regexp '{identity}' \\",
            "  --certificate-oidc-issuer https://token.actions.githubusercontent.com \\",
            "  <file>",
            "```",
            "",
            (
                "`chain.json` is the machine-readable record of the whole chain "
                "(component commits, artifact digests, measurement parameters) and "
                "carries its own bundle."
            ),
        ]
    )


def section_changes(chain: dict, prev: dict | None) -> str:
    if not prev:
        return ""
    previous = prev["release"].get("tag") or "previous release"
    lines = [
        "## Changes since the previous release",
        "",
        f"Compared with `{previous}`:",
        "",
    ]

    changes = _component_changes(chain, prev)
    lines += changes or ["- no component commit changes"]

    lines += ["", "| artifact | change |", "| --- | --- |"]
    _append_artifact_changes(lines, chain, prev)

    notes = _measurement_changes(chain, prev)
    if notes:
        lines += ["", *notes]
    return "\n".join(lines)


def _component_changes(chain: dict, prev: dict) -> list[str]:
    changes = []
    for name in COMPONENT_ORDER:
        a = component_commit(prev["components"].get(name, {}))
        b = component_commit(chain["components"].get(name, {}))
        if a != b:
            changes.append(f"- component `{name}`: `{a[:12]}` -> `{b[:12]}`")
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
            note = f"added `{cm[:12]}...` (not recorded in the previous release)"
        elif cm is None:
            note = f"removed (was `{pm[:12]}...`)"
        else:
            note = f"**changed** `{pm[:12]}...` -> `{cm[:12]}...`"
        lines.append(f"- launch measurement, {vcpu_label(vcpus)}: {note}")
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
    parts = [
        f"# {chain['release'].get('tag', 'Record build')}",
        "",
        section_identity(chain),
        "",
        section_measurement(chain),
        section_artifacts(chain),
        "",
        section_verify(args.repo, chain["release"]["mode"]),
    ]
    changes = section_changes(chain, prev)
    if changes:
        parts += ["", changes]
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", required=True, help="chain.json produced by this build")
    ap.add_argument(
        "--previous", default=None, help="chain.json of the previous release"
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default="verirelay/release")
    args = ap.parse_args()

    with open(args.chain, encoding="utf-8") as f:
        chain = json.load(f)
    prev = _read_previous_chain(args.previous)
    body = _render_release_notes(chain, args, prev)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"release notes written: {args.out} ({len(body)} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
