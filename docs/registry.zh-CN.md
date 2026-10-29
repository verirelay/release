[English](registry.md) | 简体中文

# Registry 发布

publish 模式为每个启动度量签署一条构建记录，作为 release 资产 `build-records.json` 上传，并注册到 Registry。

## 配置

这些 secret 属于 GitHub environment `release`，该 environment 必须限定为 `main` 分支。publish、replay 和 revoke 三个 job 都在其中运行。

| Secret | 值 |
| --- | --- |
| `REGISTRY_URL` | Registry 的 HTTPS base URL |
| `REGISTRY_API_PWD` | 通过 `X-API-Key` 发送的明文密码 |
| `REGISTRY_RELEASE_KEY` | Release Key 的 32 字节 Ed25519 原始 seed，编码为带 padding 的标准 base64 |

三个 secret 都未设置时，publish 与 replay 打印 notice 并跳过 Registry，不生成 `build-records.json`。只设置了一部分或密钥无效时，job 失败。

seed 对应的公钥必须与 Registry 的 `RELEASE_PUBLIC_KEY` 一致。注册与撤销都先通过 `/v1/public-key` 校验匹配，再发送密码。

注册在 GitHub release 创建**之后**执行。Registry 失败会使 job 失败，但 release 仍保持已发布。

## Release Key

在本地生成新密钥对。seed 输出保存为 secret，不得提交到仓库或打印到 CI 日志。

```bash
openssl genpkey -algorithm ed25519 -out release-key.pem
echo "REGISTRY_RELEASE_KEY=$(openssl pkey -in release-key.pem -outform DER | tail -c 32 | openssl base64 -A)"
echo "RELEASE_PUBLIC_KEY=$(openssl pkey -in release-key.pem -pubout -outform DER | tail -c 32 | openssl base64 -A | tr '+/' '-_' | tr -d '=')"
```

新生成的密钥对不会匹配 Registry 当前使用的公钥。继续使用原有 seed，或同时更新 Registry 的 `RELEASE_PUBLIC_KEY` 与本仓库的 secret `REGISTRY_RELEASE_KEY`。

## 构建记录字段

Release 为 `chain.json` 中 `launch_measurements` 的每一条签署一个 `verirelay-build-record/1` statement。其 canonical 编码和签名覆盖的字节见 Registry 的 [api.zh-CN.md](https://github.com/verirelay/registry/blob/main/docs/api.zh-CN.md) 中「签名写入」一节。

| 字段 | 值 |
| --- | --- |
| `repository` | `${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}` |
| `git_commit` | release checkout HEAD |
| `git_tag` | `chain.json` 自动生成的 `release.tag`，也是 GitHub release 的 tag |
| `measurement` | 某一条 `launch_measurements` 的无前缀小写 SHA-384 启动度量 |
| `committed_at` | HEAD 的 committer 时间，转为 UTC 的 RFC3339 `YYYY-MM-DDTHH:MM:SSZ` 格式 |

`build-records.json` 是签名构建记录的 JSON 数组。每条为 `{"statement": {...}, "signature": "..."}`，其中 Ed25519 签名为无 padding 的 base64url。

Registry 已有身份相同的构建记录时（HTTP 409），只有已存记录完全相同，注册才算成功。记录不同则失败。

## 重放注册

修复注册失败后，手动触发 **registry-replay** 并传入 `release_tag`。它按原签名注册已发布 release 的 `build-records.json` 中的每条记录。

重放需要 `build-records.json` 资产。若签名或上传失败，先生成并上传该资产。未配置 Registry 时发布的 release 没有可重放的资产。

## 撤销发布

手动触发 **registry-revoke** 并传入 `release_tag` 与 `reason`。它为 Registry 中该 tag 每条尚未撤销的构建记录签署并提交一个 `verirelay-revocation/1` statement：

| 字段 | 值 |
| --- | --- |
| `repository`、`git_commit`、`git_tag`、`measurement` | 取自签名的构建记录 |
| `reason` | 去掉首尾空白的 `reason` 输入，1 到 1000 字节 |
| `revoked_at` | 运行时间，转为 UTC 的 RFC3339 `YYYY-MM-DDTHH:MM:SSZ` 格式 |

撤销要求三个 secret 都已设置，Registry 中没有该 tag 的构建记录时失败。
