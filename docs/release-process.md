English | [简体中文](release-process.zh-CN.md)

# Release process

A release is two human-gated runs over the same build. `record` yields a candidate baseline. `publish` holds the artifacts to that baseline and ships them.

## Release identity

`versions.lock` pins build inputs and reviewed artifact baselines, not the release name. Publish mode tags the release `rel-YYYY.MM.DD-N` with the UTC date, where `N` is one plus the number of existing releases and tags with that date.

`chain.json`, the release notes, the GitHub release, and the Registry Build Records share this tag.

Release runs are serialized. Publication stops instead of reusing a tag when:

- the tag cannot be chosen
- the tag already exists
- the UTC date changes before the tag is created

In the last case, rerun publish mode.

## Component pins

Component pins land through reviewed pull requests opened by the `pin` workflow. Run it from Actions with `component` and `commit`. Each pin resets the `[expected]` baselines that depend on the component:

| Component | Baselines reset |
| --- | --- |
| verirelay | `verirelay_bin_sha256`, `initrd_sha256` |
| veriforge | `initrd_sha256`, `ovmf_sha256` |

Every pinned commit must be reachable from its repository's default branch. The `pin` workflow and CI check this with:

```bash
python3 scripts/release.py check-pins --lock versions.lock
```

The workflow opens pull requests with the `PIN_TOKEN` secret, falling back to `github.token` when it is unset.

## Two-stage release

| Mode | Run |
| --- | --- |
| `record` | Actions → `release` → Run workflow → `mode=record` |
| `publish` | Actions → `release` → Run workflow → `mode=publish`, from `main` |

`record` yields artifacts checked against the lock, with computed Launch Measurements and a candidate `[expected]` section in the run summary. Nothing is published.

`publish` yields artifacts matching every `[expected]` digest, with:

- the Launch Measurements
- `chain.json`
- release notes
- cosign keyless signatures
- GitHub artifact attestations
- a GitHub release
- Registry Build Records

Signing, publication, and Registry registration run in a separate job in the GitHub environment `release`. That environment holds the Registry secrets and must be restricted to `main`.

The `[expected]` section in `versions.lock` is the reviewed baseline. Candidate digests from `record` enter it through a normal commit.

## Local build

```bash
# Component sources at the pinned commits.
mkdir -p src
git clone https://github.com/verirelay/verirelay src/verirelay
git clone https://github.com/verirelay/veriforge src/veriforge
python3 - <<'PY'
import subprocess, tomllib
lock = tomllib.load(open('versions.lock','rb'))
for name in ('verirelay','veriforge'):
    subprocess.run(['git','-C',f'src/{name}','checkout','--quiet',lock[name]['commit']], check=True)
PY

# Parsed lock, build, Launch Measurements, and record-mode assertions.
python3 scripts/release.py lock-env --lock versions.lock
bash scripts/build-all.sh --src-dir src --out-dir /tmp/out --cache-dir /tmp/cache
python3 scripts/launch-measurement.py --lock versions.lock --artifacts-dir /tmp/out \
  --report /tmp/out/build-report.json --out /tmp/out/launch-measurement.json
python3 scripts/release.py verify --lock versions.lock --artifacts-dir /tmp/out --mode record

# Publish-mode assertions and chain.json, with [expected] filled in.
python3 scripts/release.py verify --lock versions.lock --artifacts-dir /tmp/out --mode publish
tag="$(python3 scripts/release_tag.py --repo verirelay/release)"
python3 scripts/release.py chain --lock versions.lock --artifacts-dir /tmp/out \
  --veriforge-src src/veriforge \
  --out /tmp/out/chain.json --mode publish --tag "$tag"
```

`verify` covers two kinds of consistency:

- **Artifact contents and lock**: in both modes, the Guest Manifest embedded in `build-report.json` and the reported VeriForge commit match the lock. [chain-format.md](chain-format.md) lists the compared fields.
- **Digests and baseline**: the four artifacts equal their `[expected]` digests, in publish mode.

`chain` also requires `launch-measurement.json` to cover exactly the `[launch].vcpus` list.

## Verifying a release

A release verifies through its assets alone: `chain.json`, `SHA256SUMS`, the four artifacts, and their cosign bundles as described in [provenance.md](provenance.md).
