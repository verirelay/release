#!/usr/bin/env bash
#
# Builds all four artifacts from the pinned sources. release.yml and verify.yml
# both call only this script, so "release" and "reproducibility check" run the
# exact same commands.
#
# Usage:
#   scripts/build-all.sh --src-dir DIR --out-dir DIR --cache-dir DIR [--no-cache] [--gha-cache] [--lock FILE]
#
#   --src-dir   root of the component sources already checked out to the pinned
#               commits, containing verirelay/ veriforge/
#   --out-dir   artifact output directory (four artifacts + build-report.json + artifacts.lock)
#   --cache-dir veriforge kernel/modules .deb cache directory
#   --no-cache  OVMF cold build (verify.yml uses it for the warm/cold
#               comparison; the OVMF Dockerfile inputs are fully pinned, so
#               layer-cache reuse is safe in itself)
#   --gha-cache add the GitHub Actions layer-cache backend to docker buildx (not for local use)
#   --lock      versions.lock path, defaults to versions.lock at the release repo root
#
# Environment variables:
#   OVMF_BUILD_ATTEMPTS  OVMF build retries (default 3): submodule cloning is
#                        sensitive to network flakiness, so retry lives in the
#                        orchestration layer.
#
# Preconditions (the caller's responsibility; this script does not touch the
# network or credentials):
#   $SRC/verirelay  $SRC/veriforge are clean git worktrees checked out to the
#   commits named by versions.lock.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

SRC_DIR=""
OUT_DIR=""
CACHE_DIR=""
LOCK="$RELEASE_DIR/versions.lock"
NO_CACHE=0
GHA_CACHE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src-dir) SRC_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --cache-dir) CACHE_DIR="$2"; shift 2 ;;
    --lock) LOCK="$2"; shift 2 ;;
    --no-cache) NO_CACHE=1; shift ;;
    --gha-cache) GHA_CACHE=1; shift ;;
    -h|--help) sed -nE 's/^# ?//p' "${BASH_SOURCE[0]}" | sed -n '2,/^$/p'; exit 0 ;;
    *) echo "error: unknown option $1" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }

[[ -n "$SRC_DIR" ]] || die "--src-dir is required"
[[ -n "$OUT_DIR" ]] || die "--out-dir is required"
[[ -n "$CACHE_DIR" ]] || die "--cache-dir is required"
[[ -d "$SRC_DIR/verirelay" && -d "$SRC_DIR/veriforge" ]] \
  || die "$SRC_DIR must contain verirelay/ veriforge/ source directories"

mkdir -p "$OUT_DIR" "$CACHE_DIR"

# versions.lock is the single source of parameters so each workflow does not write its own copy.
set -a
eval "$(python3 "$RELEASE_DIR/scripts/release.py" lock-env --lock "$LOCK" --format shell)"
set +a

echo "=== [1/4] verirelay init binary ==="
"$SRC_DIR/verirelay/scripts/build-init.sh" "$OUT_DIR/verirelay-linux-amd64"

echo "=== [2/4] OVMF.fd ==="
docker_opts=(--platform linux/amd64 --target export --output "type=local,dest=$OUT_DIR")
if [[ "$NO_CACHE" == "1" ]]; then docker_opts+=(--no-cache); fi
# shellcheck disable=SC2054 # Commas are part of the BuildKit cache-to value.
if [[ "$GHA_CACHE" == "1" ]]; then docker_opts+=(--cache-from type=gha --cache-to type=gha,mode=max); fi

# ovmf/build.sh clones edk2 submodules (openssl, mbedtls, ... large) from
# github.com via git submodule update, and git retries only once, so a flaky
# network fails outright. Retrying lives in the orchestration layer instead of
# changing ovmf/build.sh (an audited build input); it changes neither the
# source tree nor the artifact bytes. Only build failures are retried; the
# digest assertion is never retried.
attempts="${OVMF_BUILD_ATTEMPTS:-3}"
for attempt in $(seq 1 "$attempts"); do
  echo "--- docker buildx attempt $attempt/$attempts ---"
  if docker buildx build "${docker_opts[@]}" \
       -f "$SRC_DIR/veriforge/ovmf/Dockerfile" \
       "$SRC_DIR/veriforge"; then
    break
  fi
  if [[ "$attempt" == "$attempts" ]]; then
    die "OVMF build failed $attempts times in a row; if it was a submodule clone network error, retry later"
  fi
  echo "warning: attempt $attempt failed (usually submodule clone flakiness); retrying in 10s" >&2
  sleep 10
done

echo "=== [3/4] verify OVMF against the audit baseline ==="
( cd "$OUT_DIR" && sha256sum -c OVMF.fd.sha256 )
diff -u "$SRC_DIR/veriforge/ovmf/artifacts.lock" "$OUT_DIR/artifacts.lock" \
  || die "apt/dpkg closure does not match veriforge ovmf/artifacts.lock"
expected_ovmf="$(python3 - "$LOCK" <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as f:
    print(tomllib.load(f)["expected"]["ovmf_sha256"])
PY
)"
actual_ovmf="$(cut -d' ' -f1 "$OUT_DIR/OVMF.fd.sha256")"
if [[ -n "$expected_ovmf" ]]; then
  [[ "$expected_ovmf" == "$actual_ovmf" ]] \
    || die "OVMF.fd digest does not match versions.lock [expected].ovmf_sha256: expected=$expected_ovmf actual=$actual_ovmf"
  echo "OVMF.fd digest matches versions.lock: $actual_ovmf"
else
  echo "OVMF.fd digest (record mode): $actual_ovmf"
fi

echo "=== [4/4] initrd.img / vmlinuz / build-report.json ==="
"$SRC_DIR/veriforge/scripts/forge-initrd.sh" \
  --init "$OUT_DIR/verirelay-linux-amd64" \
  --verirelay-commit "$VERIRELAY_COMMIT" \
  --verirelay-repository "$VERIRELAY_REPOSITORY" \
  --vcpu-type "$LAUNCH_VCPU_TYPE" \
  --kernel-append "$LAUNCH_KERNEL_APPEND" \
  --out-dir "$OUT_DIR" \
  --cache "$CACHE_DIR"

echo "=== build complete: $OUT_DIR ==="
ls -l "$OUT_DIR"
