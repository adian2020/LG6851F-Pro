#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${BUILD_ID:?需要独立构建编号}"
: "${GITHUB_REPOSITORY:?需要目标仓库}"
jobs="${JOBS:-4}"
mkdir -p build-logs
python3 scripts/cloud/prepare_source.py
./scripts/prepare-public-build.sh
for package in libnl-core libnl-genl libopenssl; do
  if ! grep -qx "CONFIG_PACKAGE_${package}=y" .config; then
    printf '厂商 hostapd 运行库未选入固件：%s\n' "$package" >&2
    exit 1
  fi
done
make -j"$jobs" V=s tools/install
make -j"$jobs" V=s toolchain/install
make -j"$jobs" V=s target/linux/prepare
kernel=(build_dir/target-*/linux-mediatek_mt6990/linux-6.18.44)
test "${#kernel[@]}" -eq 1
cp development-history/V2.10/nf-nat/*.c development-history/V2.10/nf-nat/nf_internals.h "${kernel[0]}/net/netfilter/"
make -j"$jobs" V=s
python3 scripts/build-vendor-public.py --jobs "$jobs"
# HNAT is already installed by its freshly built APK; don't override its DB hash.
python3 - <<'PY'
from pathlib import Path
p=Path('files/lib/modules/6.18.44/hwnat.ko')
if p.exists():p.unlink()
PY
python3 scripts/cloud/assemble.py hostapd --jobs "$jobs"
for slot in b a; do
  python3 scripts/cloud/assemble.py slot --slot "$slot" --build-id "$BUILD_ID" --repo "$GITHUB_REPOSITORY"
  make -j"$jobs" V=s
  python3 scripts/cloud/assemble.py verify --slot "$slot"
  ./scripts/lg6851f_sign_export_boot_rootfs.sh
  "./scripts/lg6851f_make_signed_${slot}_sysupgrade.sh"
  python3 scripts/cloud/assemble.py export --slot "$slot"
done
python3 scripts/cloud/assemble.py feeds --build-id "$BUILD_ID"
cp RELEASE_NOTES.md cloud-dist/
cp build-cloud/prepared.json cloud-dist/source-build.json
python3 - <<'PY'
from pathlib import Path
import hashlib
root=Path('cloud-dist')
with (root/'SHA256SUMS.txt').open('w') as out:
 for path in sorted(root.rglob('*')):
  if path.is_file() and path.name!='SHA256SUMS.txt':out.write(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+str(path.relative_to(root))+'\n')
PY
