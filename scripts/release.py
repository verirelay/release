#!/usr/bin/env python3
"""Release tooling for verirelay/release.

Every subcommand except check-pins runs locally without GitHub, so each step
of the release pipeline can be verified before it is pushed:

    python3 scripts/release.py lock-env   --lock versions.lock
    python3 scripts/release.py check-pins --lock versions.lock
    python3 scripts/release.py verify     --lock versions.lock --artifacts-dir DIR --mode record
    python3 scripts/release.py chain      --lock versions.lock --artifacts-dir DIR \
        --veriforge-src DIR --out chain.json \
        --mode record --run-url https://github.com/.../runs/123
    python3 scripts/release.py compare    --lock versions.lock --artifacts-dir DIR --chain chain.json

Subcommands:
    lock-env    validate versions.lock and print KEY=VALUE (appendable to $GITHUB_ENV)
    check-pins  require every pinned commit to be reachable from its
                repository's default branch (GitHub compare API through gh)
    verify      assert the four artifact digests (record builds a candidate
                baseline, publish hard-asserts)
    chain       generate chain.json, the machine-readable record of the chain
    compare     compare a rebuild with a published chain.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
import urllib.parse
from typing import NoReturn

CHAIN_VERSION = "verirelay-release-chain/1"
LAUNCH_MEASUREMENT_VERSION = "verirelay-launch-measurement/1"

COMPONENTS = ("verirelay", "veriforge")

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MEASUREMENT_RE = re.compile(r"^[0-9a-f]{96}$")  # SEV-SNP measurement is 48 bytes
REPOSITORY_RE = re.compile(r"^https://[a-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~-]+)+$")
TAG_RE = re.compile(r"^rel-\d{4}\.\d{2}\.\d{2}-[1-9]\d*$")

# The two launch profile strings are defined by Protocol's guest package; here
# they only assert the contract. The actual values are read from the manifest
# embedded in build-report.json.
LAUNCH_PROFILE_VERSION = "verirelay-launch-profile/1"
MEASUREMENT_PROFILE = "sev-snp-qemu-direct-boot/1"

# sev-snp-measure settings implied by MEASUREMENT_PROFILE.
MEASURE_TOOL = "sev-snp-measure"
MEASURE_MODE = "snp"
MEASURE_VMM_TYPE = "QEMU"
MEASUREMENT_PARAMS = (
    "mode",
    "vmm_type",
    "vcpu_type",
    "guest_features",
    "kernel_append",
)

# [expected] keys in versions.lock -> artifact file names
ARTIFACTS = {
    "initrd_sha256": "initrd.img",
    "vmlinuz_sha256": "vmlinuz",
    "ovmf_sha256": "OVMF.fd",
    "verirelay_bin_sha256": "verirelay-linux-amd64",
}

# Files a rebuild produces and compares with a published chain.json.
# SHA256SUMS is derived from them.
REBUILT_FILES = (
    *ARTIFACTS.values(),
    "build-report.json",
    "artifacts.lock",
    "launch-measurement.json",
)


def die(msg: str) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_lock(path: str) -> dict:
    with open(path, "rb") as f:
        lock = tomllib.load(f)

    for section in (*COMPONENTS, "launch", "expected"):
        if section not in lock:
            die(f"versions.lock is missing section [{section}]")

    _validate_component_repositories(lock)
    _validate_launch_lock(lock)
    _validate_expected_lock(lock)
    return lock


def _validate_component_repositories(lock: dict) -> None:
    for comp in COMPONENTS:
        repo = lock[comp].get("repository", "")
        # The string goes into the Guest Manifest verbatim, which accepts only
        # the canonical form.
        if not REPOSITORY_RE.fullmatch(repo) or repo.endswith(".git"):
            die(
                f"[{comp}].repository must be a canonical https URL without .git or a trailing slash (got {repo!r})"
            )
        commit = lock[comp].get("commit", "")
        if not SHA1_RE.match(commit):
            die(f"[{comp}].commit must be a 40-char lowercase hex SHA (got {commit!r})")


def _validate_launch_lock(lock: dict) -> None:
    vcpus = lock["launch"].get("vcpus")
    if (
        not isinstance(vcpus, list)
        or not vcpus
        or any(type(n) is not int or n < 1 for n in vcpus)
        or vcpus != sorted(set(vcpus))
    ):
        die(
            f"[launch].vcpus must be a non-empty ascending list of distinct positive integers (got {vcpus!r})"
        )
    for key in ("vcpu_type", "kernel_append"):
        if not lock["launch"].get(key):
            die(f"[launch].{key} must not be empty")


def _validate_expected_lock(lock: dict) -> None:
    for key in ARTIFACTS:
        value = lock["expected"].get(key)
        if value is None:
            die(f"[expected].{key} is missing (an empty string means not anchored yet)")
        if value and not SHA256_RE.match(value):
            die(f"[expected].{key} must be a 64-char lowercase hex SHA (got {value!r})")


def repo_slug(url: str) -> str:
    """https://github.com/owner/repo -> owner/repo (for the actions/checkout repository input)."""
    return urllib.parse.urlsplit(url).path.strip("/")


def lock_env(lock: dict, fmt: str) -> None:
    values = {
        "VERIRELAY_REPOSITORY": lock["verirelay"]["repository"],
        "VERIRELAY_SLUG": repo_slug(lock["verirelay"]["repository"]),
        "VERIRELAY_COMMIT": lock["verirelay"]["commit"],
        "VERIFORGE_REPOSITORY": lock["veriforge"]["repository"],
        "VERIFORGE_SLUG": repo_slug(lock["veriforge"]["repository"]),
        "VERIFORGE_COMMIT": lock["veriforge"]["commit"],
        "LAUNCH_VCPU_TYPE": lock["launch"]["vcpu_type"],
        "LAUNCH_KERNEL_APPEND": lock["launch"]["kernel_append"],
    }
    for key, value in values.items():
        if "\n" in value:
            die(f"{key} must not contain newlines")
        if fmt == "env":
            # $GITHUB_ENV takes bare KEY=VALUE: everything after the first = is
            # the value verbatim, spaces are legal, and quoting would become
            # part of the value.
            print(f"{key}={value}")
        else:
            # shell format: safe to eval/source. Values with spaces must be
            # quoted; eval'ing the bare form above would split
            # "console=ttyS0 quiet panic=1" into assignment + command and
            # silently truncate the argument.
            print(f"{key}={shlex.quote(value)}")


def gh_api(path: str, jq: str) -> str:
    try:
        result = subprocess.run(
            ["gh", "api", path, "--jq", jq],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        die(f"gh api {path} failed: {(exc.stderr or exc.stdout or str(exc)).strip()}")
    except OSError as exc:
        die(f"cannot run gh: {exc}")
    return result.stdout.strip()


def cmd_check_pins(args: argparse.Namespace) -> int:
    lock = load_lock(args.lock)
    problems = []
    for comp in COMPONENTS:
        slug = repo_slug(lock[comp]["repository"])
        commit = lock[comp]["commit"]
        branch = gh_api(f"repos/{slug}", ".default_branch")
        # Comparing branch...commit reports "behind" or "identical" exactly
        # when the commit is an ancestor of the branch head.
        status = gh_api(
            f"repos/{slug}/compare/{urllib.parse.quote(branch)}...{commit}", ".status"
        )
        if status in ("behind", "identical"):
            print(f"[{comp}].commit is reachable from {slug}@{branch}", file=sys.stderr)
        else:
            problems.append(
                f"[{comp}].commit {commit} is not reachable from {slug}@{branch} (compare status {status!r})"
            )
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 1 if problems else 0


def ovmf_source(src: str) -> dict:
    path = os.path.join(src, "ovmf", "source.lock")
    if not os.path.isfile(path):
        die(f"{path} is missing in the veriforge sources")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    commit = re.search(r'^commit\s*=\s*"([0-9a-f]{40})"', text, re.MULTILINE)
    patch = re.search(r'^sha256\s*=\s*"([0-9a-f]{64})"', text, re.MULTILINE)
    if not commit or not patch:
        die(f"{path} lacks an edk2 commit or patch sha256")
    return {
        "edk2_commit": commit.group(1),
        "patch_sha256": patch.group(1),
        "source_lock_sha256": sha256_file(path),
    }


def check_report(lock: dict, artifacts_dir: str, mode: str) -> None:
    """Assert the manifest embedded in build-report.json equals the versions.lock declarations."""
    path = os.path.join(artifacts_dir, "build-report.json")
    if not os.path.isfile(path):
        die(
            f"{path} is missing; cannot verify the initrd-embedded manifest matches the lock"
        )

    with open(path, encoding="utf-8") as f:
        report = json.load(f)

    manifest = report["manifest"]
    veriforge_state = report.get("veriforge", {})
    problems = _report_manifest_problems(manifest, lock, veriforge_state)

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        die(
            "initrd-embedded manifest does not match versions.lock; the artifact is not the one the lock describes"
        )

    if veriforge_state.get("dirty") and mode == "publish":
        die(
            "build-report.json records veriforge dirty=true; publish must come from a clean worktree"
        )

    print("initrd-embedded manifest matches versions.lock", file=sys.stderr)
    if veriforge_state.get("dirty"):
        warn(
            "build-report.json records dirty=true (allowed in record mode, fails in publish mode)"
        )


def _report_manifest_problems(
    manifest: dict, lock: dict, veriforge_state: dict
) -> list[str]:
    problems: list[str] = []
    checks = [
        (
            f"manifest.source.{comp}.{key}",
            manifest["source"][comp][key],
            lock[comp][key],
        )
        for comp in COMPONENTS
        for key in ("repository", "commit")
    ]
    checks += [
        (
            f"manifest.launch_profile.{key}",
            manifest["launch_profile"][key],
            lock["launch"][key],
        )
        for key in ("vcpu_type", "kernel_append")
    ]
    checks.append(
        ("veriforge.commit", veriforge_state.get("commit"), lock["veriforge"]["commit"])
    )
    for desc, actual, wanted in checks:
        if actual != wanted:
            problems.append(f"{desc}: expected {wanted!r}, got {actual!r}")
    return problems


def cmd_verify(args: argparse.Namespace) -> int:
    lock = load_lock(args.lock)
    actual, missing = _hash_verify_artifacts(args.artifacts_dir)

    if missing:
        die(f"artifact directory is missing files: {', '.join(missing)}")

    # Assert artifact contents against the lock first. This is independent of
    # the expected digests and is never skipped, even in record mode: it
    # guarantees the initrd-embedded manifest really equals the lock's
    # declarations. A wrong argument (a space-containing value truncated, a URL
    # written with .git) fails here instead of becoming a baseline with a
    # different digest nobody can explain.
    check_report(lock, args.artifacts_dir, args.mode)

    expected = lock["expected"]
    mismatches = _verify_mismatches(expected, actual)
    _print_verify_digests(args.mode, actual, expected)

    if args.mode == "publish":
        return _verify_publish(expected, mismatches)
    return _verify_record(args, expected, actual, mismatches)


def _hash_verify_artifacts(artifacts_dir: str) -> tuple[dict[str, str], list[str]]:
    actual: dict[str, str] = {}
    missing: list[str] = []
    for key, filename in ARTIFACTS.items():
        path = os.path.join(artifacts_dir, filename)
        if not os.path.isfile(path):
            missing.append(filename)
            continue
        actual[key] = sha256_file(path)
    return actual, missing


def _verify_mismatches(
    expected: dict, actual: dict[str, str]
) -> list[tuple[str, str, str]]:
    mismatches = []
    for key in ARTIFACTS:
        exp = expected[key]
        if exp and exp != actual[key]:
            mismatches.append((key, exp, actual[key]))
    return mismatches


def _print_verify_digests(mode: str, actual: dict[str, str], expected: dict) -> None:
    print(f"mode: {mode}", file=sys.stderr)
    for key in ARTIFACTS:
        print(f"  {key:24s} actual={actual[key]}", file=sys.stderr)
        if expected[key]:
            print(f"  {'':24s} expect={expected[key]}", file=sys.stderr)


def _verify_publish(expected: dict, mismatches: list[tuple[str, str, str]]) -> int:
    empty = [k for k in ARTIFACTS if not expected[k]]
    if empty:
        die(
            f"[expected] must not be empty in publish mode: {', '.join(empty)}; run record mode first to build the baseline"
        )
    if mismatches:
        for key, exp, got in mismatches:
            print(
                f"error: {key} mismatch\n  expected {exp}\n  actual   {got}",
                file=sys.stderr,
            )
        return 1
    print("all digests match versions.lock", file=sys.stderr)
    return 0


def _verify_record(
    args: argparse.Namespace,
    expected: dict,
    actual: dict[str, str],
    mismatches: list[tuple[str, str, str]],
) -> int:
    # record mode: produce a candidate baseline; never fails on differences.
    lines = ["[expected]"]
    for key in ARTIFACTS:
        lines.append(f'{key} = "{actual[key]}"')
    block = "\n".join(lines)
    if args.record_out:
        with open(args.record_out, "w", encoding="utf-8") as f:
            f.write(block + "\n")
    print("candidate baseline from record mode:", file=sys.stderr)
    print(block)
    if mismatches:
        for key, exp, got in mismatches:
            warn(f"{key} differs from the existing baseline: {exp} -> {got}")
        print(
            "note: the digests above differ from the existing versions.lock baseline; replace after review.",
            file=sys.stderr,
        )
    elif any(expected[key] for key in ARTIFACTS):
        print(
            "candidate baseline matches the existing versions.lock exactly.",
            file=sys.stderr,
        )
    else:
        print(
            "versions.lock [expected] is empty (not anchored); fill it in after reviewing the candidates above.",
            file=sys.stderr,
        )
    return 0


def cmd_chain(args: argparse.Namespace) -> int:
    lock = load_lock(args.lock)
    artifacts = _chain_artifacts(args.artifacts_dir)
    ovmf = ovmf_source(args.veriforge_src)
    measurements = _chain_measurements(args.artifacts_dir, args.mode, lock)
    profile = _chain_launch_profile(args.artifacts_dir)
    chain = _build_chain(lock, args, artifacts, ovmf, profile, measurements)
    validate_chain(chain)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(chain, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"chain.json written: {args.out}", file=sys.stderr)
    return 0


def _chain_artifacts(artifacts_dir: str) -> dict[str, dict]:
    # Index by artifact file name (not the lock keys): chain.json consumers
    # get files, and provenance.subjects must be file names too.
    artifacts: dict[str, dict] = {}
    for filename in ARTIFACTS.values():
        path = os.path.join(artifacts_dir, filename)
        if not os.path.isfile(path):
            die(f"artifact directory is missing {filename}")
        artifacts[filename] = _digest_entry(path)

    for name in (
        "build-report.json",
        "artifacts.lock",
        "SHA256SUMS",
        "launch-measurement.json",
    ):
        path = os.path.join(artifacts_dir, name)
        if os.path.isfile(path):
            artifacts[name] = _digest_entry(path)
    return artifacts


def _digest_entry(path: str) -> dict:
    return {"sha256": sha256_file(path), "size": os.path.getsize(path)}


def _chain_measurements(artifacts_dir: str, mode: str, lock: dict) -> list[dict] | None:
    path = os.path.join(artifacts_dir, "launch-measurement.json")
    if not os.path.isfile(path):
        if mode == "publish":
            # fail-closed: a published chain.json must carry the measurements.
            die(
                "launch-measurement.json is missing in publish mode; "
                "run scripts/launch-measurement.py first (there is a step in release.yml)"
            )
        warn(
            "launch-measurement.json is missing (allowed in record mode, fails in publish mode)"
        )
        return None
    measurements = read_launch_measurements(path)
    measured = [entry["vcpus"] for entry in measurements]
    if measured != lock["launch"]["vcpus"]:
        die(
            f"launch-measurement.json covers vcpus {measured}, but versions.lock [launch].vcpus is {lock['launch']['vcpus']}"
        )
    return measurements


def read_launch_measurements(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or doc.get("version") != LAUNCH_MEASUREMENT_VERSION:
        die(f"{path} must be an object with version {LAUNCH_MEASUREMENT_VERSION}")
    entries = doc.get("launch_measurements")
    validate_launch_measurements(entries, "launch-measurement.json")
    return entries


def _chain_launch_profile(artifacts_dir: str) -> dict:
    # The two launch profile strings are read from the artifact-embedded
    # manifest, then asserted against the contract values.
    report_path = os.path.join(artifacts_dir, "build-report.json")
    if not os.path.isfile(report_path):
        die(
            "build-report.json is missing in the artifact directory; chain.json needs its launch_profile"
        )
    with open(report_path, encoding="utf-8") as f:
        profile = (json.load(f).get("manifest") or {}).get("launch_profile") or {}
    if not profile:
        die("build-report.json has no manifest.launch_profile")
    if profile.get("version") != LAUNCH_PROFILE_VERSION:
        die(
            f"manifest.launch_profile.version={profile.get('version')!r} does not equal contract value {LAUNCH_PROFILE_VERSION}"
        )
    if profile.get("measurement_profile") != MEASUREMENT_PROFILE:
        die(
            f"manifest.launch_profile.measurement_profile={profile.get('measurement_profile')!r} does not equal contract value {MEASUREMENT_PROFILE}"
        )
    return profile


def _build_chain(
    lock: dict,
    args: argparse.Namespace,
    artifacts: dict[str, dict],
    ovmf: dict,
    profile: dict,
    measurements: list[dict] | None,
) -> dict:
    chain = {
        "version": CHAIN_VERSION,
        "release": _chain_release(args),
        "components": _chain_components(lock, ovmf),
        "launch": _chain_launch(lock, profile),
        "artifacts": artifacts,
        "provenance": {
            "subjects": sorted(artifacts.keys()),
            "verified_against_lock": args.mode == "publish",
        },
    }
    if measurements is not None:
        chain["launch_measurements"] = measurements
    return chain


def _chain_release(args: argparse.Namespace) -> dict:
    if args.mode == "publish" and not TAG_RE.fullmatch(args.tag):
        die("publish mode requires --tag rel-YYYY.MM.DD-N")
    release = {
        "mode": args.mode,
        "lock_sha256": sha256_file(args.lock),
        "run_url": args.run_url,
    }
    if args.mode == "publish":
        release["tag"] = args.tag
    return release


def _chain_components(lock: dict, ovmf: dict) -> dict:
    components = {
        comp: {
            "repository": lock[comp]["repository"],
            "commit": lock[comp]["commit"],
        }
        for comp in COMPONENTS
    }
    components["ovmf"] = ovmf
    return components


def _chain_launch(lock: dict, profile: dict) -> dict:
    return {
        "version": profile["version"],
        "measurement_profile": profile["measurement_profile"],
        "vcpu_type": lock["launch"]["vcpu_type"],
        "kernel_append": lock["launch"]["kernel_append"],
    }


def validate_launch_measurements(entries: object, where: str) -> None:
    """Validate a launch_measurements list so no partial or illegal value is signed into chain.json."""
    if not isinstance(entries, list) or not entries:
        die(f"{where} launch_measurements must be a non-empty list")
    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            die(f"{where} launch_measurements entries must be objects")
        vcpus = entry.get("vcpus")
        if type(vcpus) is not int or vcpus < 1 or vcpus in seen:
            die(
                f"{where} launch_measurements has an invalid or duplicate vcpus {vcpus!r}"
            )
        seen.add(vcpus)
        label = f"{where} launch_measurements[vcpus={vcpus}]"
        measurement = entry.get("measurement")
        if not isinstance(measurement, str) or not MEASUREMENT_RE.fullmatch(
            measurement
        ):
            die(f"{label}.measurement is not 96 lowercase hex chars: {measurement!r}")
        params = entry.get("params")
        if not isinstance(params, dict) or any(
            k not in params for k in MEASUREMENT_PARAMS
        ):
            die(f"{label}.params must contain {', '.join(MEASUREMENT_PARAMS)}")
        if params["mode"] != MEASURE_MODE or params["vmm_type"] != MEASURE_VMM_TYPE:
            die(
                f"{label}.params mode/vmm_type is {params['mode']!r}/{params['vmm_type']!r}, "
                f"inconsistent with measurement_profile={MEASUREMENT_PROFILE!r}"
            )
        tool = entry.get("tool")
        if (
            not isinstance(tool, dict)
            or tool.get("name") != MEASURE_TOOL
            or not tool.get("version")
        ):
            die(f"{label}.tool is invalid: {tool!r}")


def validate_chain(chain: dict) -> None:
    """Dependency-free structural check of chain.schema.json (CI does not install jsonschema)."""
    _validate_chain_required_fields(chain)
    _validate_chain_artifacts(chain)
    _validate_chain_components(chain)
    _validate_chain_provenance(chain)
    if "launch_measurements" in chain:
        validate_launch_measurements(chain["launch_measurements"], "chain.json")
    _validate_chain_launch(chain)


def _validate_chain_required_fields(chain: dict) -> None:
    required = (
        "version",
        "release",
        "components",
        "launch",
        "artifacts",
        "provenance",
    )
    for key in required:
        if key not in chain:
            die(f"chain.json is missing {key}")
    if chain["version"] != CHAIN_VERSION:
        die(f"chain.json version must be {CHAIN_VERSION}")
    release = chain["release"]
    if release["mode"] == "publish" and not TAG_RE.fullmatch(release.get("tag", "")):
        die("published chain.json requires a release tag rel-YYYY.MM.DD-N")


def _validate_chain_artifacts(chain: dict) -> None:
    for filename in ARTIFACTS.values():
        if filename not in chain["artifacts"]:
            die(f"chain.json artifacts is missing required artifact {filename}")
    for name, entry in chain["artifacts"].items():
        if not SHA256_RE.match(entry["sha256"]):
            die(f"chain.json artifacts.{name}.sha256 has an invalid format")
        if not isinstance(entry["size"], int) or entry["size"] <= 0:
            die(f"chain.json artifacts.{name}.size is invalid")


def _validate_chain_components(chain: dict) -> None:
    for name, entry in chain["components"].items():
        if "commit" in entry and not SHA1_RE.match(entry["commit"]):
            die(f"chain.json components.{name}.commit has an invalid format")


def _validate_chain_provenance(chain: dict) -> None:
    if not chain["provenance"]["subjects"]:
        die("chain.json provenance.subjects must not be empty")
    if not SHA256_RE.match(chain["release"]["lock_sha256"]):
        die("chain.json release.lock_sha256 has an invalid format")


def _validate_chain_launch(chain: dict) -> None:
    if chain["launch"]["version"] != LAUNCH_PROFILE_VERSION:
        die(f"chain.json launch.version must be {LAUNCH_PROFILE_VERSION}")
    if chain["launch"]["measurement_profile"] != MEASUREMENT_PROFILE:
        die(f"chain.json launch.measurement_profile must be {MEASUREMENT_PROFILE}")


def cmd_compare(args: argparse.Namespace) -> int:
    with open(args.chain, encoding="utf-8") as f:
        chain = json.load(f)
    validate_chain(chain)

    problems = []
    if sha256_file(args.lock) != chain["release"]["lock_sha256"]:
        problems.append(f"{args.lock} is not the versions.lock recorded in chain.json")

    for name in REBUILT_FILES:
        entry = chain["artifacts"].get(name)
        path = os.path.join(args.artifacts_dir, name)
        if entry is None:
            problems.append(f"chain.json does not record {name}")
            continue
        if not os.path.isfile(path):
            problems.append(f"the rebuild is missing {name}")
            continue
        digest = sha256_file(path)
        if digest == entry["sha256"]:
            print(f"match  {name:24s} {digest}", file=sys.stderr)
        else:
            problems.append(f"{name}: rebuilt {digest}, published {entry['sha256']}")

    published = {
        e["vcpus"]: e["measurement"] for e in chain.get("launch_measurements", [])
    }
    if not published:
        problems.append("chain.json records no launch measurements")
    rebuilt = {
        e["vcpus"]: e["measurement"]
        for e in read_launch_measurements(
            os.path.join(args.artifacts_dir, "launch-measurement.json")
        )
    }
    for vcpus in sorted(set(published) | set(rebuilt)):
        want, got = published.get(vcpus), rebuilt.get(vcpus)
        if want == got:
            print(f"match  launch measurement, {vcpus} vCPU: {got}", file=sys.stderr)
        else:
            problems.append(
                f"launch measurement, {vcpus} vCPU: rebuilt {got}, published {want}"
            )

    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("the rebuild matches the published chain.json", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lock-env", help="validate the lock and print KEY=VALUE")
    p.add_argument("--lock", default="versions.lock")
    p.add_argument(
        "--format",
        choices=["env", "shell"],
        default="env",
        help="env: bare KEY=VALUE for $GITHUB_ENV; shell: quoted, safe to eval/source",
    )

    p = sub.add_parser(
        "check-pins", help="require pinned commits on their default branches"
    )
    p.add_argument("--lock", default="versions.lock")

    p = sub.add_parser("verify", help="assert artifact digests")
    p.add_argument("--lock", default="versions.lock")
    p.add_argument("--artifacts-dir", required=True)
    p.add_argument("--mode", choices=["record", "publish"], required=True)
    p.add_argument(
        "--record-out",
        help="in record mode, write the candidate [expected] section to this file",
    )

    p = sub.add_parser("chain", help="generate chain.json")
    p.add_argument("--lock", default="versions.lock")
    p.add_argument("--artifacts-dir", required=True)
    p.add_argument("--veriforge-src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--mode", choices=["record", "publish"], required=True)
    p.add_argument("--run-url", default="")
    p.add_argument(
        "--tag", default="", help="generated publish tag; unused in record mode"
    )

    p = sub.add_parser("compare", help="compare a rebuild with a published chain.json")
    p.add_argument("--lock", default="versions.lock")
    p.add_argument("--artifacts-dir", required=True)
    p.add_argument("--chain", required=True, help="the published chain.json")

    args = ap.parse_args()
    if args.cmd == "lock-env":
        lock_env(load_lock(args.lock), args.format)
        return 0
    return {
        "check-pins": cmd_check_pins,
        "verify": cmd_verify,
        "chain": cmd_chain,
        "compare": cmd_compare,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
