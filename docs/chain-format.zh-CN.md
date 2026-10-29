[English](chain-format.md) | 简体中文

# 发布证据链格式

## `chain.json`

`chain.json` 是一次发布的机器可读发布证据链，由 `chain.schema.json` 定义。它只由可从公开信息复现的事实构成：各组件 commit 加上 `versions.lock`。

| 字段 | 内容 |
| --- | --- |
| `version` | `verirelay-release-chain/1` |
| `release.mode` | `record` 或 `publish` |
| `release.tag` | 自动生成的 tag，仅 publish 模式携带 |
| `release.lock_sha256` | `versions.lock` 的 digest |
| `release.run_url` | workflow run 的 URL |
| `components.verirelay`、`components.veriforge` | lock 中的 `repository` 与 `commit` |
| `components.ovmf` | `edk2_commit`、`patch_sha256` 与 `source_lock_sha256` |
| `artifacts` | 以文件名为键，记录每个文件的 `sha256` 与 `size` |
| `launch` | 启动配置的 `version`、`measurement_profile`、`vcpu_type` 与 `kernel_append` |
| `launch_measurements` | `launch-measurement.json` 的 `launch_measurements` 列表 |
| `provenance.subjects` | 签名覆盖的文件名 |
| `provenance.verified_against_lock` | 标记产物 digest 已绑定 lock 的 `[expected]` 基线 |

- `artifacts` 记录 `initrd.img`、`vmlinuz`、`OVMF.fd` 和 `verirelay-linux-amd64`。`build-report.json`、`artifacts.lock`、`SHA256SUMS` 和 `launch-measurement.json` 存在时也一并记录。
- `launch` 与产物内嵌的来宾启动声明和契约值一致。
- `launch_measurements` 为 `[launch].vcpus` 中每个值各记一条，顺序与 lock 一致。publish 模式必需。

## `launch-measurement.json`

`launch-measurement.json` 记录发布的启动产物集在每个 vCPU 数下的启动度量。说明见 [launch-measurement.zh-CN.md](launch-measurement.zh-CN.md)。

| 字段 | 内容 |
| --- | --- |
| `version` | `verirelay-launch-measurement/1` |
| `launch_measurements` | 每个 vCPU 数一条 |
| `authority` | 说明这些值为离线计算，真实证明报告才是权威 |

每条 `launch_measurements` 记录包含：

- `vcpus`
- `measurement`：96 位小写 hex
- `params`：`mode`（`snp`）、`vmm_type`（`QEMU`）、`vcpu_type`、`guest_features` 与 `kernel_append`
- `tool`：`name`（`sev-snp-measure`）与 `version`

## `build-report.json`

`build-report.json` 是产物目录中 VeriForge 的构建报告。它也是 `chain.json` 中启动配置的来源。字段说明见 VeriForge 的 [manifest.zh-CN.md](https://github.com/verirelay/veriforge/blob/main/docs/manifest.zh-CN.md) 中「`build-report.json`」一节。

两种模式下，以下字段与 `versions.lock` 不一致时，`scripts/release.py verify` 都会失败：

| `build-report.json` 字段 | `versions.lock` 键 |
| --- | --- |
| `manifest.source.verirelay.repository`、`manifest.source.verirelay.commit` | `[verirelay].repository`、`[verirelay].commit` |
| `manifest.source.veriforge.repository`、`manifest.source.veriforge.commit` | `[veriforge].repository`、`[veriforge].commit` |
| `manifest.launch_profile.vcpu_type`、`kernel_append` | `[launch].vcpu_type`、`kernel_append` |
| `veriforge.commit` | `[veriforge].commit` |

启动度量要求 `manifest.launch_profile.kernel_hashes` 为 `true`。正式发布总是来自干净的 VeriForge 工作树（`veriforge.dirty` 为 `false`）。

## `artifacts.lock`

`artifacts.lock` 是 OVMF 构建环境完整的 dpkg 包闭包，与 VeriForge 的 `ovmf/artifacts.lock` 完全一致。
