import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import release

SCRIPTS = Path(__file__).parent
spec = importlib.util.spec_from_file_location(
    "release_notes", SCRIPTS / "release-notes.py"
)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)

LOCK = """[verirelay]
repository = "https://github.com/verirelay/verirelay"
commit = "{verirelay}"

[veriforge]
repository = "{veriforge_repository}"
commit = "{veriforge}"

[launch]
vcpus = {vcpus}
vcpu_type = "EPYC-v4"
kernel_append = "{kernel_append}"

[expected]
initrd_sha256 = ""
vmlinuz_sha256 = ""
ovmf_sha256 = ""
verirelay_bin_sha256 = ""
"""
DEFAULTS = {
    "verirelay": "a" * 40,
    "veriforge": "b" * 40,
    "veriforge_repository": "https://github.com/verirelay/veriforge",
    "vcpus": "[1, 2, 4]",
    "kernel_append": "console=ttyS0 quiet panic=1",
}

# Prints a measurement derived from --vcpus so each size gets its own value.
FAKE_MEASURE = """#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if args == ["--version"]:
    print("sev-snp-measure 0.0.13")
    raise SystemExit
vcpus = int(args[args.index("--vcpus") + 1])
print(format(vcpus, "x").rjust(96, "0"))
"""


def run(*args):
    return subprocess.run(
        [sys.executable, *map(str, args)], check=False, capture_output=True, text=True
    )


class Fixture:
    """A build output directory, a lock, and a VeriForge source tree."""

    def __init__(self, base, **lock):
        self.base = base
        self.lock = base / "versions.lock"
        self.lock.write_text(LOCK.format(**{**DEFAULTS, **lock}))
        self.out = base / "out"
        self.out.mkdir()
        for name in release.REBUILT_FILES[:4]:
            (self.out / name).write_bytes(name.encode())
        (self.out / "artifacts.lock").write_text("closure\n")
        report = {
            "manifest": {
                "version": "verirelay-guest-manifest/1",
                "source": {
                    "verirelay": {
                        "repository": "https://github.com/verirelay/verirelay",
                        "commit": DEFAULTS["verirelay"],
                    },
                    "veriforge": {
                        "repository": DEFAULTS["veriforge_repository"],
                        "commit": DEFAULTS["veriforge"],
                    },
                },
                "launch_profile": {
                    "version": "verirelay-launch-profile/1",
                    "measurement_profile": "sev-snp-qemu-direct-boot/1",
                    "vcpu_type": "EPYC-v4",
                    "guest_features": "0x1",
                    "kernel_hashes": True,
                    "kernel_append": DEFAULTS["kernel_append"],
                },
            },
            "veriforge": {"commit": DEFAULTS["veriforge"], "dirty": False},
        }
        (self.out / "build-report.json").write_text(json.dumps(report))
        self.veriforge = base / "veriforge"
        (self.veriforge / "ovmf").mkdir(parents=True)
        (self.veriforge / "ovmf/source.lock").write_text(
            f'commit = "{"c" * 40}"\nsha256 = "{"d" * 64}"\n'
        )
        self.tool = base / "sev-snp-measure"
        self.tool.write_text(FAKE_MEASURE)
        self.tool.chmod(0o755)

    def measure(self):
        return run(
            SCRIPTS / "launch-measurement.py",
            "--lock", self.lock,
            "--artifacts-dir", self.out,
            "--report", self.out / "build-report.json",
            "--out", self.out / "launch-measurement.json",
            "--bin", self.tool,
        )  # fmt: skip

    def chain(self, mode="publish"):
        return run(
            SCRIPTS / "release.py", "chain",
            "--lock", self.lock,
            "--artifacts-dir", self.out,
            "--veriforge-src", self.veriforge,
            "--out", self.base / "chain.json",
            "--mode", mode,
            "--tag", "rel-2026.10.29-1",
        )  # fmt: skip

    def compare(self):
        return run(
            SCRIPTS / "release.py", "compare",
            "--lock", self.lock,
            "--artifacts-dir", self.out,
            "--chain", self.base / "chain.json",
        )  # fmt: skip


class LockTests(unittest.TestCase):
    def lock_env(self, **lock):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "versions.lock"
            path.write_text(LOCK.format(**{**DEFAULTS, **lock}))
            return run(SCRIPTS / "release.py", "lock-env", "--lock", path)

    def test_repository_lock_parses(self):
        result = run(
            SCRIPTS / "release.py",
            "lock-env",
            "--lock",
            SCRIPTS.parent / "versions.lock",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lock_env_keys(self):
        result = self.lock_env()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [line.split("=", 1)[0] for line in result.stdout.splitlines()],
            [
                "VERIRELAY_REPOSITORY",
                "VERIRELAY_SLUG",
                "VERIRELAY_COMMIT",
                "VERIFORGE_REPOSITORY",
                "VERIFORGE_SLUG",
                "VERIFORGE_COMMIT",
                "LAUNCH_VCPU_TYPE",
                "LAUNCH_KERNEL_APPEND",
            ],
        )
        self.assertIn("VERIFORGE_SLUG=verirelay/veriforge\n", result.stdout)

    def test_vcpus_must_be_an_ascending_list(self):
        for vcpus in ("1", "[]", "[2, 1]", "[1, 1]", "[0, 1]", "[true]", '["1"]'):
            with self.subTest(vcpus=vcpus):
                result = self.lock_env(vcpus=vcpus)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[launch].vcpus", result.stderr)

    def test_kernel_append_is_required(self):
        result = self.lock_env(kernel_append="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[launch].kernel_append", result.stderr)

    def test_repository_must_be_canonical(self):
        for repository in (
            "https://github.com/verirelay/veriforge.git",
            "https://github.com/verirelay/veriforge/",
            "http://github.com/verirelay/veriforge",
            "https://user@github.com/verirelay/veriforge",
            "https://GitHub.com/verirelay/veriforge",
            "https://github.com/verirelay/veriforge?x=1",
            "https://github.com",
        ):
            with self.subTest(repository=repository):
                result = self.lock_env(veriforge_repository=repository)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("canonical https URL", result.stderr)


class CheckPinsTests(unittest.TestCase):
    def check(self, statuses):
        calls = []

        def gh_api(path, jq):
            calls.append(path)
            return "main" if jq == ".default_branch" else statuses.pop(0)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(release, "gh_api", side_effect=gh_api),
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            path = Path(directory) / "versions.lock"
            path.write_text(LOCK.format(**DEFAULTS))
            code = release.cmd_check_pins(release.argparse.Namespace(lock=str(path)))
        return code, calls, error.getvalue()

    def test_reachable_pins_pass(self):
        code, calls, _ = self.check(["behind", "identical"])
        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [
                "repos/verirelay/verirelay",
                f"repos/verirelay/verirelay/compare/main...{'a' * 40}",
                "repos/verirelay/veriforge",
                f"repos/verirelay/veriforge/compare/main...{'b' * 40}",
            ],
        )

    def test_unreachable_pin_fails(self):
        for status in ("ahead", "diverged"):
            with self.subTest(status=status):
                code, _, error = self.check(["behind", status])
                self.assertEqual(code, 1)
                self.assertIn("[veriforge].commit", error)
                self.assertNotIn("[verirelay].commit " + "a" * 40, error)


class MeasurementChainTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.fixture = Fixture(Path(temp.name))

    def test_one_measurement_per_vcpu_count(self):
        result = self.fixture.measure()
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads((self.fixture.out / "launch-measurement.json").read_text())
        self.assertEqual(doc["version"], "verirelay-launch-measurement/1")
        params = {
            "mode": "snp",
            "vmm_type": "QEMU",
            "vcpu_type": "EPYC-v4",
            "guest_features": "0x1",
            "kernel_append": DEFAULTS["kernel_append"],
        }
        self.assertEqual(
            doc["launch_measurements"],
            [
                {
                    "vcpus": vcpus,
                    "measurement": format(vcpus, "x").rjust(96, "0"),
                    "params": params,
                    "tool": {"name": "sev-snp-measure", "version": "0.0.13"},
                }
                for vcpus in (1, 2, 4)
            ],
        )

    def test_manifest_must_match_lock(self):
        path = self.fixture.out / "build-report.json"
        report = json.loads(path.read_text())
        report["manifest"]["launch_profile"]["kernel_append"] = "console=ttyS0"
        path.write_text(json.dumps(report))
        result = self.fixture.measure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kernel_append", result.stderr)

    def test_chain_carries_every_measurement(self):
        self.assertEqual(self.fixture.measure().returncode, 0)
        result = self.fixture.chain()
        self.assertEqual(result.returncode, 0, result.stderr)
        chain = json.loads((self.fixture.base / "chain.json").read_text())
        measured = json.loads(
            (self.fixture.out / "launch-measurement.json").read_text()
        )
        self.assertEqual(chain["version"], "verirelay-release-chain/1")
        self.assertEqual(chain["launch_measurements"], measured["launch_measurements"])
        self.assertEqual(set(chain["components"]), {"verirelay", "veriforge", "ovmf"})
        self.assertEqual(
            chain["launch"],
            {
                "version": "verirelay-launch-profile/1",
                "measurement_profile": "sev-snp-qemu-direct-boot/1",
                "vcpu_type": "EPYC-v4",
                "kernel_append": DEFAULTS["kernel_append"],
            },
        )
        self.assertIn("launch-measurement.json", chain["artifacts"])

    def test_chain_rejects_measurements_for_other_sizes(self):
        self.assertEqual(self.fixture.measure().returncode, 0)
        self.fixture.lock.write_text(LOCK.format(**{**DEFAULTS, "vcpus": "[1, 2]"}))
        result = self.fixture.chain()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("covers vcpus [1, 2, 4]", result.stderr)

    def test_publish_chain_requires_measurements(self):
        result = self.fixture.chain()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "launch-measurement.json is missing in publish mode", result.stderr
        )
        self.assertEqual(self.fixture.chain("record").returncode, 0)

    def test_verify_compares_manifest_with_lock(self):
        args = ["verify", "--lock", self.fixture.lock, "--artifacts-dir"]
        result = run(
            SCRIPTS / "release.py", *args, self.fixture.out, "--mode", "record"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.fixture.out / "build-report.json"
        report = json.loads(path.read_text())
        report["manifest"]["source"]["verirelay"]["commit"] = "e" * 40
        path.write_text(json.dumps(report))
        result = run(
            SCRIPTS / "release.py", *args, self.fixture.out, "--mode", "record"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest.source.verirelay.commit", result.stderr)

    def test_compare_with_published_chain(self):
        self.assertEqual(self.fixture.measure().returncode, 0)
        self.assertEqual(self.fixture.chain().returncode, 0)
        result = self.fixture.compare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("launch measurement, 4 vCPU", result.stderr)

        path = self.fixture.out / "launch-measurement.json"
        doc = json.loads(path.read_text())
        doc["launch_measurements"][1]["measurement"] = "f" * 96
        path.write_text(json.dumps(doc))
        (self.fixture.out / "vmlinuz").write_bytes(b"other kernel")
        result = self.fixture.compare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error: vmlinuz: rebuilt", result.stderr)
        self.assertIn("error: launch-measurement.json: rebuilt", result.stderr)
        self.assertIn(
            "error: launch measurement, 2 vCPU: rebuilt " + "f" * 96, result.stderr
        )

    def test_compare_requires_the_release_lock(self):
        self.assertEqual(self.fixture.measure().returncode, 0)
        self.assertEqual(self.fixture.chain().returncode, 0)
        self.fixture.lock.write_text(self.fixture.lock.read_text() + "\n")
        result = self.fixture.compare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not the versions.lock recorded in chain.json", result.stderr)

    def test_release_notes_list_every_size(self):
        self.assertEqual(self.fixture.measure().returncode, 0)
        self.assertEqual(self.fixture.chain().returncode, 0)
        chain = json.loads((self.fixture.base / "chain.json").read_text())
        text = notes.section_measurement(chain)
        for vcpus, label in ((1, "1 vCPU"), (2, "2 vCPUs"), (4, "4 vCPUs")):
            measurement = format(vcpus, "x").rjust(96, "0")
            self.assertIn(f"| {vcpus} | `{measurement}` |", text)
            self.assertIn(f"### {label}\n", text)
            self.assertIn(f"  --vcpus {vcpus} --vcpu-type EPYC-v4", text)
        self.assertEqual(text.count("uvx --from sev-snp-measure==0.0.13"), 3)
        self.assertIn("--append 'console=ttyS0 quiet panic=1'", text)

        previous = json.loads(json.dumps(chain))
        previous["launch_measurements"][0]["measurement"] = "e" * 96
        del previous["launch_measurements"][2]
        changes = notes.section_changes(chain, previous)
        self.assertIn(
            "- launch measurement, 1 vCPU: **changed** `eeeeeeeeeeee...`", changes
        )
        self.assertIn("- launch measurement, 2 vCPUs: unchanged", changes)
        self.assertIn("- launch measurement, 4 vCPUs: added", changes)

    def test_cosign_identity_is_main_only(self):
        text = notes.section_verify("verirelay/release", "publish")
        self.assertIn(
            "'^https://github\\.com/verirelay/release/\\.github/workflows/release\\.yml@refs/heads/main$'",
            text,
        )


if __name__ == "__main__":
    unittest.main()
