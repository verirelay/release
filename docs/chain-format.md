English | [简体中文](chain-format.zh-CN.md)

# Release Evidence Chain format

## `chain.json`

`chain.json` is the machine-readable Release Evidence Chain of one release, defined by `chain.schema.json`. It is built only from facts reproducible from public information: the component commits plus `versions.lock`.

| Field | Contents |
| --- | --- |
| `version` | `verirelay-release-chain/1`. |
| `release.mode` | `record` or `publish`. |
| `release.tag` | The generated tag, in publish mode only. |
| `release.lock_sha256` | Digest of `versions.lock`. |
| `release.run_url` | URL of the workflow run. |
| `components.verirelay`, `components.veriforge` | `repository` and `commit` from the lock. |
| `components.ovmf` | `edk2_commit`, `patch_sha256`, and `source_lock_sha256`. |
| `artifacts` | `sha256` and `size` of each file, keyed by file name. |
| `launch` | `version`, `measurement_profile`, `vcpu_type`, and `kernel_append` of the launch profile. |
| `launch_measurements` | The `launch_measurements` list of `launch-measurement.json`. |
| `provenance.subjects` | The file names covered by signing. |
| `provenance.verified_against_lock` | Marks a chain whose artifact digests are bound to the lock's `[expected]` baselines. |

- `artifacts` covers `initrd.img`, `vmlinuz`, `OVMF.fd`, and `verirelay-linux-amd64`. It also covers `build-report.json`, `artifacts.lock`, `SHA256SUMS`, and `launch-measurement.json` where present.
- `launch` is consistent with both the artifact-embedded Guest Manifest and the contract values.
- `launch_measurements` has one entry per `[launch].vcpus` value, in lock order. It is required in publish mode.

## `launch-measurement.json`

`launch-measurement.json` holds the Launch Measurements of the release's Boot Artifact Set, one per vCPU count, as described in [launch-measurement.md](launch-measurement.md).

| Field | Contents |
| --- | --- |
| `version` | `verirelay-launch-measurement/1`. |
| `launch_measurements` | One entry per vCPU count. |
| `authority` | A note that the values are computed offline and a real Attestation Report is authoritative. |

Each `launch_measurements` entry has:

- `vcpus`
- `measurement`: 96 lowercase hex characters
- `params`: `mode` (`snp`), `vmm_type` (`QEMU`), `vcpu_type`, `guest_features`, and `kernel_append`
- `tool`: `name` (`sev-snp-measure`) and `version`

## `build-report.json`

`build-report.json` is VeriForge's Build Report inside the artifact directory. It is also the source of the launch profile in `chain.json`. Its fields are described in VeriForge's [manifest.md](https://github.com/verirelay/veriforge/blob/main/docs/manifest.md), section "`build-report.json`".

In both modes, `scripts/release.py verify` fails unless these fields equal `versions.lock`:

| `build-report.json` field | `versions.lock` key |
| --- | --- |
| `manifest.source.verirelay.repository`, `manifest.source.verirelay.commit` | `[verirelay].repository`, `[verirelay].commit` |
| `manifest.source.veriforge.repository`, `manifest.source.veriforge.commit` | `[veriforge].repository`, `[veriforge].commit` |
| `manifest.launch_profile.vcpu_type`, `kernel_append` | `[launch].vcpu_type`, `kernel_append` |
| `veriforge.commit` | `[veriforge].commit` |

The Launch Measurements require `manifest.launch_profile.kernel_hashes` to be `true`. A published release always comes from a clean VeriForge tree (`veriforge.dirty` is `false`).

## `artifacts.lock`

`artifacts.lock` is the full dpkg package closure of the OVMF build environment, identical to VeriForge's `ovmf/artifacts.lock`.
