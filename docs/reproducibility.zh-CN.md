[English](reproducibility.md) | 简体中文

# 可复现性验证

## 重建最新发布

`verify` workflow（每周一次，也可手动触发）在发布 tag 处检出本仓库，重建最新的 `rel-*` 发布。满足以下条件即通过：

- 分别用热缓存与冷缓存（`--no-cache`）构建两次，六个文件逐字节相同
- 构建与该发布的 `chain.json` 一致：
  - `versions.lock` 对比 `release.lock_sha256`
  - 六个文件和 `launch-measurement.json` 的 digest 对比 `artifacts`
  - 逐条对比 `launch_measurements`

手动执行相同步骤时，在发布 tag 处的检出中进行，并把该发布的 `chain.json` 放在当前目录：

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

`src/` 下的组件源码按 [release-process.zh-CN.md](release-process.zh-CN.md) 中「本地构建」一节检出。

冷构建从 github.com 克隆 edk2 子模块，因此依赖网络。`OVMF_BUILD_ATTEMPTS`（默认 3）设定 OVMF 构建的尝试次数。

## OVMF 编译阶段确定性

`scripts/check-ovmf-determinism.sh` 离线检查编译阶段。它基于缓存的 builder 镜像，在两个全新容器中各完整重编译一次 OVMF，要求两份 digest 一致。`versions.lock`（或 `--lock` 指定的文件）设定了 `[expected].ovmf_sha256` 时，digest 还必须与之一致。

```bash
scripts/check-ovmf-determinism.sh --veriforge-src src/veriforge
```

该检查不覆盖 apt 安装与子模块克隆阶段。这两个阶段由 `artifacts.lock` 闭包和 `source.lock` commit 固定。
