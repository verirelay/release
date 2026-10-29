English | [简体中文](reproducibility.zh-CN.md)

# Reproducibility verification

## Rebuilding the latest release

The `verify` workflow (weekly and on manual dispatch) rebuilds the latest `rel-*` release from this repository at the release tag. It passes when:

- a warm and a cold (`--no-cache`) layer-cache build produce byte-identical copies of six files;
- the build matches the release's `chain.json`:
  - `versions.lock` against `release.lock_sha256`;
  - the digests of the six files and `launch-measurement.json` against `artifacts`;
  - every entry of `launch_measurements`.

The same steps by hand, from a checkout at the release tag with the release's `chain.json` in the current directory:

```bash
bash scripts/build-all.sh --src-dir src --out-dir /tmp/warm --cache-dir /tmp/cache
bash scripts/build-all.sh --src-dir src --out-dir /tmp/cold --cache-dir /tmp/cache --no-cache
for f in initrd.img vmlinuz OVMF.fd verirelay-linux-amd64 build-report.json artifacts.lock; do
  cmp "/tmp/warm/$f" "/tmp/cold/$f" || echo "$f is not reproducible"
done
python3 scripts/launch-measurement.py --lock versions.lock --artifacts-dir /tmp/warm \
  --report /tmp/warm/build-report.json --out /tmp/warm/launch-measurement.json
python3 scripts/release.py compare --lock versions.lock --artifacts-dir /tmp/warm --chain chain.json
```

The component sources under `src/` are checked out as in [release-process.md](release-process.md), section "Local build".

The cold build clones the edk2 submodules from github.com, so it depends on the network. `OVMF_BUILD_ATTEMPTS` (default 3) sets the number of OVMF build attempts.

## OVMF compile-stage determinism

`scripts/check-ovmf-determinism.sh` checks the compile stage offline. It recompiles OVMF twice in fresh containers from the cached builder image and requires identical digests. When `[expected].ovmf_sha256` is set in `versions.lock` (or the file given by `--lock`), the digest must also match it.

```bash
scripts/check-ovmf-determinism.sh --veriforge-src src/veriforge
```

This check does not cover the apt-install and submodule-clone stages. Those are pinned by the `artifacts.lock` closure and the `source.lock` commit.
