import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIGEST = hashlib.sha256(b"firmware").hexdigest()


class OvmfDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.src = self.base / "src"
        for component in ("veriforge", "verirelay"):
            (self.src / component).mkdir(parents=True)
        ovmf = self.src / "veriforge/ovmf"
        ovmf.mkdir()
        (ovmf / "Dockerfile").touch()
        (ovmf / "artifacts.lock").write_text("closure\n")
        (ovmf / "source.lock").write_text('commit = "source"\n')
        for component, script in (
            ("verirelay", "build-init.sh"),
            ("veriforge", "forge-initrd.sh"),
        ):
            directory = self.src / component / "scripts"
            directory.mkdir()
            self.executable(directory / script, "#!/bin/sh\nexit 0\n")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.executable(
            self.bin / "docker",
            """#!/usr/bin/env python3
import hashlib
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args[0] == 'buildx':
    for arg in args:
        if arg.startswith('type=local,dest='):
            out = Path(arg.split('=', 2)[2])
            (out / 'OVMF.fd').write_bytes(b'firmware')
            digest = hashlib.sha256(b'firmware').hexdigest()
            (out / 'OVMF.fd.sha256').write_text(digest + '  OVMF.fd\\n')
            (out / 'artifacts.lock').write_text('closure\\n')
elif 'rev-parse HEAD' in args[-1] and 'sha256sum' not in args[-1]:
    print('source')
else:
    counter = Path(os.environ['COUNTER'])
    count = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(count + 1))
    data = b'different' if count and os.environ.get('DIFFERENT') else b'firmware'
    print(hashlib.sha256(data).hexdigest())
""",
        )

    def executable(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    def run_script(self, script, expected, different=False):
        lock = self.base / "versions.lock"
        lock.write_text(
            f"""[verirelay]
repository = "https://github.com/verirelay/verirelay"
commit = "{"a" * 40}"

[veriforge]
repository = "https://github.com/verirelay/veriforge"
commit = "{"b" * 40}"

[launch]
vcpus = [1, 2, 4, 8, 16]
vcpu_type = "EPYC-v4"
kernel_append = "console=ttyS0 quiet panic=1"

[expected]
initrd_sha256 = ""
vmlinuz_sha256 = ""
ovmf_sha256 = "{expected}"
verirelay_bin_sha256 = ""
"""
        )
        args = ["bash", str(ROOT / "scripts" / script), "--lock", str(lock)]
        if script == "build-all.sh":
            args += [
                "--src-dir",
                str(self.src),
                "--out-dir",
                str(self.base / "out"),
                "--cache-dir",
                str(self.base / "cache"),
            ]
        else:
            args += ["--veriforge-src", str(self.src / "veriforge")]
        return subprocess.run(
            args,
            check=False,
            env={
                **os.environ,
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "COUNTER": str(self.base / "counter"),
                "DIFFERENT": "1" if different else "",
            },
            capture_output=True,
            text=True,
        )

    def test_record_mode(self):
        for script in ("build-all.sh", "check-ovmf-determinism.sh"):
            with self.subTest(script=script):
                result = self.run_script(script, "")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"OVMF.fd digest (record mode): {DIGEST}", result.stdout)

    def test_matching_lock(self):
        for script in ("build-all.sh", "check-ovmf-determinism.sh"):
            with self.subTest(script=script):
                result = self.run_script(script, DIGEST)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("record mode", result.stdout)

    def test_mismatching_lock(self):
        for script in ("build-all.sh", "check-ovmf-determinism.sh"):
            with self.subTest(script=script):
                result = self.run_script(script, "0" * 64)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("does not match versions.lock", result.stderr)

    def test_different_compiles_in_record_mode(self):
        result = self.run_script("check-ovmf-determinism.sh", "", different=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the two compiles differ", result.stderr)


if __name__ == "__main__":
    unittest.main()
