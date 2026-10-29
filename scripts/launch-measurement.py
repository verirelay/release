#!/usr/bin/env python3
"""Compute the SEV-SNP launch measurements (offline expected values).

What they are and are not
-------------------------
Each value equals the `measurement` field the firmware writes into the
attestation report at guest boot, provided the launch configuration matches
its parameters exactly. One Boot Artifact Set serves every vCPU count, and the
measurement depends on the vCPU count, so there is one value per entry of
[launch].vcpus. They are expected values computed offline; the authoritative
value can only come from a real report.

Parameter sources (not hard-coded)
----------------------------------
  vcpus / vcpu_type / kernel_append   <- [launch] in versions.lock
  guest_features                      <- the manifest embedded in build-report.json
  OVMF.fd / vmlinuz / initrd.img      <- artifacts of this build

The sources are cross-asserted: a manifest/lock mismatch fails outright
instead of producing plausible-looking values.

Usage
-----
    python3 scripts/launch-measurement.py \\
        --lock versions.lock --artifacts-dir dist \\
        --report dist/build-report.json --out dist/launch-measurement.json

By default it runs `uvx --from sev-snp-measure==<pinned version>`; when the
tool is already installed locally, pass `--bin` to avoid the network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import NoReturn

from release import (
    LAUNCH_MEASUREMENT_VERSION,
    MEASURE_MODE,
    MEASURE_TOOL,
    MEASURE_VMM_TYPE,
    MEASUREMENT_RE,
    load_lock,
)

# Pin the tool version: the measurement is a function of inputs and tool version, so version drift changes the published values.
TOOL_VERSION = "0.0.13"

VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


def die(msg: str) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def measure_prefix(bin_path: str | None) -> list[str]:
    if bin_path:
        return [bin_path]
    if shutil.which("uvx"):
        # --from makes uvx install the specified version in an isolated env, keeping the runner Python clean.
        return ["uvx", "--from", f"{MEASURE_TOOL}=={TOOL_VERSION}", MEASURE_TOOL]
    if shutil.which(MEASURE_TOOL):
        return [MEASURE_TOOL]
    die(f"neither uvx nor {MEASURE_TOOL} found; pass --bin or install uv first")


def tool_version(prefix: list[str]) -> str:
    """Read the tool's self-reported version. Fail if it cannot be read; never
    claim the pinned version without evidence.

    The measurement is a function of the tool version: a value computed by a
    different version is not comparable and is not visibly different.
    """
    try:
        out = subprocess.run(
            [*prefix, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as e:
        die(f"cannot run {MEASURE_TOOL} --version: {e}")
    m = VERSION_RE.search(out.stdout + out.stderr)
    if not m:
        die(
            f"cannot parse a version from {MEASURE_TOOL} --version output: {out.stdout + out.stderr!r}"
        )
    observed = m.group(1)
    if observed != TOOL_VERSION:
        die(
            f"{MEASURE_TOOL} actual version {observed} does not match pinned {TOOL_VERSION}; "
            "the measurement is a function of the tool version and values from another version are not comparable"
        )
    return observed


def _load_guest_features(report_path: str, launch: dict) -> object:
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
    profile = (report.get("manifest") or {}).get("launch_profile") or {}
    if not profile:
        die(
            f"{report_path} has no manifest.launch_profile; cannot determine measurement parameters"
        )

    # Cross-assert: the parameters that went into the manifest must match the
    # lock's declarations, otherwise the result is the measurement of a
    # different configuration.
    for key in ("vcpu_type", "kernel_append"):
        if profile.get(key) != launch[key]:
            die(
                f"manifest.launch_profile.{key}={profile.get(key)!r} does not match versions.lock [launch].{key}={launch[key]!r}"
            )

    guest_features = profile.get("guest_features")
    if not guest_features:
        die(
            "manifest.launch_profile.guest_features is missing; the measurement needs it"
        )

    # The tool gets --kernel/--initrd/--append below, which only matches real
    # hardware when QEMU kernel hashes are enabled (the hash tables are
    # injected and measured).
    if profile.get("kernel_hashes") is not True:
        die(
            "manifest.launch_profile.kernel_hashes is not true; "
            "this command computes with kernel hashes in the measurement, and a mismatched configuration does not correspond to real hardware"
        )
    return guest_features


def _measurement_artifacts(artifacts_dir: str) -> dict[str, str]:
    artifacts = {}
    for name in ("OVMF.fd", "vmlinuz", "initrd.img"):
        path = os.path.join(artifacts_dir, name)
        if not os.path.isfile(path):
            die(f"artifact directory is missing {name}")
        artifacts[name] = path
    return artifacts


def _measurement_command(
    vcpus: int, params: dict, artifacts: dict[str, str], prefix: list[str]
) -> list[str]:
    return [
        *prefix,
        "--mode",
        params["mode"],
        "--vmm-type",
        params["vmm_type"],
        "--vcpus",
        str(vcpus),
        "--vcpu-type",
        params["vcpu_type"],
        "--guest-features",
        str(params["guest_features"]),
        "--ovmf",
        artifacts["OVMF.fd"],
        "--kernel",
        artifacts["vmlinuz"],
        "--initrd",
        artifacts["initrd.img"],
        "--append",
        params["kernel_append"],
        "--output-format",
        "hex",
    ]


def _run_measurement(cmd: list[str]) -> str:
    print("$ " + " ".join(shlex.quote(c) for c in cmd), file=sys.stderr)
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        die(f"{MEASURE_TOOL} exited with code {proc.returncode}")

    # uvx writes install logs to stderr, so the value can only be on the last stdout line.
    value = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
    if not MEASUREMENT_RE.match(value):
        die(f"output is not a 48-byte hex measurement: {value!r}")
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", required=True)
    ap.add_argument("--artifacts-dir", required=True)
    ap.add_argument("--report", required=True, help="path to build-report.json")
    ap.add_argument(
        "--out", required=True, help="output path for launch-measurement.json"
    )
    ap.add_argument(
        "--bin", default=None, help="sev-snp-measure executable (uvx by default)"
    )
    args = ap.parse_args()

    launch = load_lock(args.lock)["launch"]
    params = {
        "mode": MEASURE_MODE,
        "vmm_type": MEASURE_VMM_TYPE,
        "vcpu_type": launch["vcpu_type"],
        "guest_features": _load_guest_features(args.report, launch),
        "kernel_append": launch["kernel_append"],
    }
    artifacts = _measurement_artifacts(args.artifacts_dir)
    prefix = measure_prefix(args.bin)
    tool = {"name": MEASURE_TOOL, "version": tool_version(prefix)}

    measurements = []
    for vcpus in launch["vcpus"]:
        value = _run_measurement(_measurement_command(vcpus, params, artifacts, prefix))
        print(f"launch measurement, {vcpus} vCPU: {value}", file=sys.stderr)
        measurements.append(
            {"vcpus": vcpus, "measurement": value, "params": params, "tool": tool}
        )

    record = {
        "version": LAUNCH_MEASUREMENT_VERSION,
        "launch_measurements": measurements,
        "authority": (
            "Offline expected values. The authoritative value is the measurement "
            "field of the attestation report at guest boot; a mismatch means the "
            "actual launch configuration differs from the parameters recorded here."
        ),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"written: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
