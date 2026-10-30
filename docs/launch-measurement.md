English | [简体中文](launch-measurement.zh-CN.md)

# Launch Measurement

Every release records the SEV-SNP Launch Measurements of its Boot Artifact Set, one per vCPU count, in the release notes and in the `launch_measurements` list of `chain.json`.

One Boot Artifact Set boots with every listed vCPU count. Each value is computed offline. It is the `measurement` expected in the Attestation Report of a CVM launched with exactly the recorded parameters.

A real Attestation Report is the authority. A differing `measurement` points to an unrecorded part of the launch chain (VMM version, machine type, EFI variables, extra ACPI tables, ...).

## Parameters

| Parameter | Source |
| --- | --- |
| `vcpus` | `[launch].vcpus` in `versions.lock`, an ascending list such as `[1, 2, 4, 8, 16]` |
| `vcpu_type` / `kernel_append` | `[launch]` in `versions.lock`, equal to the Guest Manifest embedded in `build-report.json` |
| `guest_features` | the Guest Manifest embedded in `build-report.json` (defined in Protocol's [guest.md](https://github.com/verirelay/protocol/blob/main/docs/guest.md)) |
| `OVMF.fd` / `vmlinuz` / `initrd.img` | artifacts of this build |

The values assume `kernel_hashes` is enabled. They also depend on the `sev-snp-measure` version, recorded as `tool.version` in each entry.

## Recomputing

The release's own artifacts are enough. Take the `launch_measurements` entry of `chain.json` whose `vcpus` equals the CVM's vCPU count. In the command below:

- `<version>` is its `tool.version`
- `<vcpus>` is its `vcpus`
- the other placeholders are in its `params`

The output must equal its `measurement`:

```bash
uvx --from sev-snp-measure==<version> sev-snp-measure --mode snp --vmm-type QEMU \
  --vcpus <vcpus> --vcpu-type <vcpu_type> --guest-features <guest_features> \
  --ovmf OVMF.fd --kernel vmlinuz --initrd initrd.img \
  --append "<kernel_append>" --output-format hex
```

The release notes carry this command with every value filled in, as one loop over all vCPU counts.
