[English](launch-measurement.md) | 简体中文

# 启动度量

每个发布都在 release notes 和 `chain.json` 的 `launch_measurements` 列表中记录启动产物集的 SEV-SNP 启动度量，每个 vCPU 数一个。

同一个启动产物集可以按列出的每个 vCPU 数启动。每个值都离线计算得出。它是按记录参数原样启动的机密虚拟机（CVM）的证明报告中 `measurement` 的期望值。

真实证明报告是权威。`measurement` 不同说明启动链中有未记录的部分（VMM 版本、machine type、EFI 变量、额外 ACPI 表等）。

## 参数

| 参数 | 来源 |
| --- | --- |
| `vcpus` | `versions.lock` 的 `[launch].vcpus`，升序列表，例如 `[1, 2, 4, 8, 16]` |
| `vcpu_type` / `kernel_append` | `versions.lock` 的 `[launch]`，与 `build-report.json` 内嵌的来宾启动声明一致 |
| `guest_features` | `build-report.json` 内嵌的来宾启动声明（定义见 Protocol 的 [guest.zh-CN.md](https://github.com/verirelay/protocol/blob/main/docs/guest.zh-CN.md)） |
| `OVMF.fd` / `vmlinuz` / `initrd.img` | 本次构建的产物 |

这些值假定 `kernel_hashes` 已开启。它们还取决于 `sev-snp-measure` 版本，版本记录在每条记录的 `tool.version` 中。

## 重新计算

发布自身的产物就足够。在 `chain.json` 的 `launch_measurements` 中取 `vcpus` 等于 CVM vCPU 数的那一条。下面的命令中：

- `<version>` 取其 `tool.version`
- `<vcpus>` 取其 `vcpus`
- 其余占位符取其 `params`

输出必须等于其 `measurement`：

```bash
uvx --from sev-snp-measure==<version> sev-snp-measure --mode snp --vmm-type QEMU \
  --vcpus <vcpus> --vcpu-type <vcpu_type> --guest-features <guest_features> \
  --ovmf OVMF.fd --kernel vmlinuz --initrd initrd.img \
  --append "<kernel_append>" --output-format hex
```

release notes 中为每个 vCPU 数附有填好全部参数的这条命令。
