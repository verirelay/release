[English](release-process.md) | 简体中文

# 发布流程

一次发布是同一构建上两次人工触发的运行。`record` 产出候选基线。`publish` 让产物对齐该基线并发布。

## 发布身份

`versions.lock` 固定构建输入与审阅后的产物基线，不配置发布名称。publish 模式按 UTC 日期把发布 tag 定为 `rel-YYYY.MM.DD-N`，`N` 为该日期已有的 release 与 tag 数量加一。

`chain.json`、release notes、GitHub release 和 Registry 构建记录使用同一个 tag。

发布运行串行执行。以下情况下，发布停止而不复用 tag：

- 无法选定 tag
- tag 已存在
- 创建 tag 前 UTC 日期已变化

日期变化时，重新运行 publish 模式即可。

## 组件 pin

组件 pin 通过 `pin` workflow 开出的待审 PR 落地。在 Actions 中带 `component` 和 `commit` 运行该 workflow。每次 pin 都会重置依赖该组件的 `[expected]` 基线：

| 组件 | 重置的基线 |
| --- | --- |
| verirelay | `verirelay_bin_sha256`、`initrd_sha256` |
| veriforge | `initrd_sha256`、`ovmf_sha256` |

每个 pin 住的 commit 都必须能从其仓库的默认分支到达。`pin` workflow 和 CI 用以下命令检查这一点：

```bash
python3 scripts/release.py check-pins --lock versions.lock
```

该 workflow 用 secret `PIN_TOKEN` 开 PR，未设置时回落到 `github.token`。

## 两段式发布

| 模式 | 运行 |
| --- | --- |
| `record` | Actions → `release` → Run workflow → `mode=record` |
| `publish` | 在 `main` 上 Actions → `release` → Run workflow → `mode=publish` |

`record` 产出按 lock 检查的产物与已计算的启动度量，并在 run summary 中写出一段候选 `[expected]`。不发布任何内容。

`publish` 产出与每个 `[expected]` digest 一致的产物，并附带：

- 启动度量
- `chain.json`
- release notes
- cosign keyless 签名
- GitHub artifact attestation
- GitHub release
- Registry 构建记录

签名、发布和 Registry 注册在 GitHub environment `release` 中的单独 job 里执行。该 environment 保存 Registry secret，必须限定为 `main`。

`versions.lock` 中的 `[expected]` 是经过审阅的基线。`record` 的候选 digest 通过普通 commit 进入其中。

## 本地构建

```bash
# 检出 pin 住的组件源码
mkdir -p src
git clone https://github.com/verirelay/verirelay src/verirelay
git clone https://github.com/verirelay/veriforge src/veriforge
python3 - <<'PY'
import subprocess, tomllib
lock = tomllib.load(open('versions.lock','rb'))
for name in ('verirelay','veriforge'):
    subprocess.run(['git','-C',f'src/{name}','checkout','--quiet',lock[name]['commit']], check=True)
PY

# 解析 lock, 构建, 启动度量, record 模式断言
python3 scripts/release.py lock-env --lock versions.lock
bash scripts/build-all.sh --src-dir src --out-dir /tmp/out --cache-dir /tmp/cache
python3 scripts/launch-measurement.py --lock versions.lock --artifacts-dir /tmp/out \
  --report /tmp/out/build-report.json --out /tmp/out/launch-measurement.json
python3 scripts/release.py verify --lock versions.lock --artifacts-dir /tmp/out --mode record

# 填好 [expected] 后的 publish 模式断言与 chain.json
python3 scripts/release.py verify --lock versions.lock --artifacts-dir /tmp/out --mode publish
tag="$(python3 scripts/release_tag.py --repo verirelay/release)"
python3 scripts/release.py chain --lock versions.lock --artifacts-dir /tmp/out \
  --veriforge-src src/veriforge \
  --out /tmp/out/chain.json --mode publish --tag "$tag"
```

`verify` 覆盖两类一致性：

- **产物内容与 lock**：两种模式下，`build-report.json` 内嵌的来宾启动声明和报告的 VeriForge commit 都与 lock 一致。比较的字段见 [chain-format.zh-CN.md](chain-format.zh-CN.md)。
- **digest 与基线**：四个产物与各自的 `[expected]` digest 一致，适用于 publish 模式。

`chain` 还要求 `launch-measurement.json` 恰好覆盖 `[launch].vcpus` 列表。

## 验证一个发布

一个发布仅凭其资产即可验证：`chain.json`、`SHA256SUMS`、四个产物及其 cosign bundle，见 [provenance.zh-CN.md](provenance.zh-CN.md)。
