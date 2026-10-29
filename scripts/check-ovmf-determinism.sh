#!/usr/bin/env bash
#
# Determinism check of the OVMF compile stage (independent of github.com
# network quality).
#
# Rationale: the bottleneck of a full cold build (`build-all.sh --no-cache`) is
# cloning the edk2 submodules from github.com, which fails outright on a flaky
# network before compilation is even reached. This script uses an equivalent,
# controlled split instead:
#   1. Reuse the cached builder image: it already contains the pinned edk2
#      source tree (build.sh verified rev-parse == source.lock's commit), the
#      applied patches, and the pinned apt toolchain;
#   2. Run a full recompile (Build/ deleted first) in two fresh containers of
#      the same image;
#   3. Compare the two outputs byte for byte and against versions.lock [expected].ovmf_sha256 when set.
#
# It proves: the same pinned source + the same pinned toolchain -> the same bytes.
# It does not cover the apt-install and submodule-clone stages (those are bound
# by the artifacts.lock closure assertion, the source.lock commit assertion,
# and the FROM scratch export stage).
#
# Usage:
#   scripts/check-ovmf-determinism.sh --veriforge-src DIR [--image TAG] [--lock FILE]
#
# A non-zero exit code means non-deterministic output or a digest mismatch.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOCK="$SCRIPT_DIR/../versions.lock"
VERIFORGE_SRC=""
IMAGE="ovmf-builder-determinism"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --veriforge-src) VERIFORGE_SRC="$2"; shift 2 ;;
    --lock) LOCK="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    -h|--help) sed -nE 's/^# ?//p' "${BASH_SOURCE[0]}" | sed -n '2,/^$/p'; exit 0 ;;
    *) echo "error: unknown option $1" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }
[[ -n "$VERIFORGE_SRC" ]] || die "--veriforge-src is required (path to the veriforge checkout)"
[[ -f "$VERIFORGE_SRC/ovmf/Dockerfile" ]] || die "$VERIFORGE_SRC/ovmf/Dockerfile does not exist"
python3 "$SCRIPT_DIR/release.py" lock-env --lock "$LOCK" >/dev/null
expected_ovmf="$(python3 - "$LOCK" <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as f:
    print(tomllib.load(f)["expected"]["ovmf_sha256"])
PY
)"

echo "=== [1/3] reuse the cached builder image ($IMAGE) ==="
docker buildx build --platform linux/amd64 --target builder -t "$IMAGE" \
  -f "$VERIFORGE_SRC/ovmf/Dockerfile" "$VERIFORGE_SRC" >/tmp/ovmf-builder.log 2>&1 \
  || die "could not build the builder image; see /tmp/ovmf-builder.log"

echo "--- source tree identity inside the image (should match ovmf/source.lock) ---"
docker run --rm "$IMAGE" bash -lc 'git -C /build/edk2 rev-parse HEAD'
grep -m1 '^commit' "$VERIFORGE_SRC/ovmf/source.lock"

# Reproduce the compile-related environment of ovmf/build.sh; the only difference is no re-clone.
compile() {
  docker run --rm "$IMAGE" bash -lc '
    set -euo pipefail
    cd /build/edk2
    rm -rf Build
    export TZ=UTC LC_ALL=C.UTF-8 LANG=C.UTF-8 PYTHONHASHSEED=0
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    umask 022
    export SOURCE_DATE_EPOCH="$(git -C /build/edk2 show -s --format=%ct "$(git -C /build/edk2 rev-parse HEAD)")"
    export WORKSPACE=/build/edk2 PACKAGES_PATH=/build/edk2
    set +u; source edksetup.sh >/dev/null; set -u
    build -a X64 -p OvmfPkg/AmdSev/AmdSevX64.dsc -t GCC -b RELEASE >/dev/null
    sha256sum Build/AmdSev/RELEASE_GCC/FV/OVMF.fd | cut -d" " -f1
  '
}

echo "=== [2/3] two independent recompiles ==="
a="$(compile)"; echo "compile A: $a"
b="$(compile)"; echo "compile B: $b"

echo "=== [3/3] conclusion ==="
[[ "$a" == "$b" ]] || die "the two compiles differ (OVMF compile stage is not reproducible)"
echo "TWO-COMPILES-IDENTICAL"
if [[ -n "$expected_ovmf" ]]; then
  [[ "$a" == "$expected_ovmf" ]] || die "compile result does not match versions.lock [expected].ovmf_sha256"
  echo "MATCHES-LOCK-DIGEST"
else
  echo "OVMF.fd digest (record mode): $a"
fi
