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

The cold build fetches source over the network: it clones the edk2 submodules from github.com and downloads the kernel.org tarball. `OVMF_BUILD_ATTEMPTS` (default 3) sets the number of attempts for both the OVMF and the kernel Docker builds.

The OVMF and kernel builds install only the `.deb` files that VeriForge's `debs.lock` files pin by SHA-256. `build-all.sh` fetches them into `<cache-dir>/debs`, falling back across the Ubuntu snapshot, Launchpad, and VeriForge's mirror, and reuses files that already match, so a later build with the same `--cache-dir` needs no package source. See VeriForge's [Kernel and OVMF packages](https://github.com/verirelay/veriforge/blob/main/docs/reproducibility.md#kernel-and-ovmf-packages).

## OVMF compile-stage determinism

`scripts/check-ovmf-determinism.sh` checks the compile stage offline. It recompiles OVMF twice in fresh containers from the cached builder image and requires identical digests. When `[expected].ovmf_sha256` is set in `versions.lock` (or the file given by `--lock`), the digest must also match it.

```bash
scripts/check-ovmf-determinism.sh --veriforge-src src/veriforge
```

It fetches the OVMF build's `.deb` files into `<veriforge-src>/dist/debs/ovmf` first. This check does not cover the package-install and submodule-clone stages. Those are pinned by `debs.lock`, the `artifacts.lock` closure, and the `source.lock` commit.
