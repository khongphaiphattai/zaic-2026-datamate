#!/usr/bin/env bash
# package_freeze_solution.sh: Build, offline-verify (--network none), and package
# zaic2026-datamate:freeze for ZAIC 2026 Portal "Test Solution" (MD5) & Final Freeze (SHA256).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE_TAG="${IMAGE_TAG:-zaic2026-datamate:freeze}"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/artifacts/freeze-package}"
VERIFY_DIR="${VERIFY_DIR:-/tmp/zaic_docker_verify}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data/public_test}"
ARCHIVE_PATH="$OUT_DIR/zaic2026-datamate-freeze.tar.gz"
MANIFEST_PATH="$OUT_DIR/checksums.txt"

mkdir -p "$OUT_DIR"
rm -rf "$VERIFY_DIR"
mkdir -p "$VERIFY_DIR"

echo "================================================================================"
echo "[1/4] Building Docker image: $IMAGE_TAG"
echo "================================================================================"
docker build -t "$IMAGE_TAG" .

echo "================================================================================"
echo "[2/4] Running offline container parity check (--network none, read-only /data)"
echo "================================================================================"
docker run --rm \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,exec,nosuid,size=2g \
  -v "$DATA_DIR:/data:ro" \
  -v "$VERIFY_DIR:/result:rw" \
  "$IMAGE_TAG"

if [[ ! -f "$VERIFY_DIR/submission.jsonl" ]]; then
  echo "ERROR: Container did not produce $VERIFY_DIR/submission.jsonl" >&2
  exit 1
fi

CONTAINER_SHA256="$(sha256sum "$VERIFY_DIR/submission.jsonl" | awk '{print $1}')"
echo "Container submission.jsonl SHA256: $CONTAINER_SHA256"

if [[ -f "$REPO_ROOT/submission.jsonl" ]]; then
  LOCAL_SHA256="$(sha256sum "$REPO_ROOT/submission.jsonl" | awk '{print $1}')"
  echo "Local     submission.jsonl SHA256: $LOCAL_SHA256"
  if [[ "$CONTAINER_SHA256" != "$LOCAL_SHA256" ]]; then
    echo "ERROR: Parity mismatch between container and root submission.jsonl!" >&2
    exit 1
  fi
  echo "Parity Verified: 100% byte-identical!"
fi

echo "================================================================================"
echo "[3/4] Exporting compressed Docker image to: $ARCHIVE_PATH"
echo "================================================================================"
docker save "$IMAGE_TAG" | gzip -1 > "$ARCHIVE_PATH"

echo "================================================================================"
echo "[4/4] Computing MD5 (for Portal Test Solution before 15/11 23:59) & SHA256"
echo "================================================================================"
IMG_MD5="$(md5sum "$ARCHIVE_PATH" | awk '{print $1}')"
IMG_SHA256="$(sha256sum "$ARCHIVE_PATH" | awk '{print $1}')"
IMG_SIZE="$(du -h "$ARCHIVE_PATH" | awk '{print $1}')"

cat > "$MANIFEST_PATH" <<EOF
ZAIC 2026 DataMate — Docker Freeze & Test Solution Package
==========================================================
Image Tag:                 $IMAGE_TAG
Archive File:              $ARCHIVE_PATH
Archive Size:              $IMG_SIZE
Submission SHA256:         $CONTAINER_SHA256

[Portal Tab "Test Solution" — Closes 23:59 15/11/2026 (Max 1 run/day)]
MD5 Checksum:              $IMG_MD5

[Final Solution Freeze — Closes 23:59 16/11/2026]
SHA256 Checksum:           $IMG_SHA256
EOF

cat "$MANIFEST_PATH"
echo "================================================================================"
echo "Saved checksum manifest to: $MANIFEST_PATH"
