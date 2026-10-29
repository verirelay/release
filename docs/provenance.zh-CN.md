[English](provenance.md) | 简体中文

# 来源证明与验证

publish 模式用 cosign keyless 签名以下文件：

- 四个产物
- `build-report.json`
- `artifacts.lock`
- `chain.json`
- `SHA256SUMS`
- `launch-measurement.json`

`build-records.json` 改由 Release Key 签名，见 [registry.zh-CN.md](registry.zh-CN.md)。

## Cosign keyless

每个签名的文件都附带 `<file>.cosign.bundle` release 资产。

```bash
cosign verify-blob \
  --bundle initrd.img.cosign.bundle \
  --certificate-identity-regexp '^https://github\.com/verirelay/release/\.github/workflows/release\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  initrd.img
```

签名公开记录在 **Rekor 透明日志** 中（只含 digest 与签名者身份）。归档 bundle 与证书链即可完全离线验证。

## GitHub artifact attestation

publish 模式还会为 release 资产生成 GitHub 原生 artifact attestation。cosign bundle 是主要机制。

## 证据证明什么

- 产物字节来自本仓库 `main` 上的 `release.yml`，即证书中记录的身份。
- `chain.json` 把四个产物 digest 和启动度量绑定到各组件 commit 与 `versions.lock`（`lock_sha256`），因此发布证据链可离线重放。
