#!/usr/bin/env python3
"""Prepare an experimental full source build; never consume release-runtime."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'build-cloud'
REPAIRS=ROOT/'development-history/systemfix-source/systemfix-20261021-30/driver-build/repair-source'


def check():
    required=['.config','scripts/build-vendor-public.py','scripts/vendor-build-part.py',
              'scripts/lg6851f_sign_export_boot_rootfs.sh','lg6851f_sign_tool/reference/boot_b-reference.img',
              'development-history/V2.10/hostapd-native/hostapd/.config',
              'files-slots/a/lib/upgrade/platform.sh','files-slots/b/lib/upgrade/platform.sh']
    required += [str((REPAIRS/name).relative_to(ROOT)) for name in ['parser-repair.c','action-repair.c','pmksa-repair.c']]
    missing=[name for name in required if not (ROOT/name).is_file()]
    if missing:raise RuntimeError('Missing source inputs: '+', '.join(missing))
    image=ROOT/'target/linux/mediatek/mt6990/base-files/usr/lib/firmware/lg6851f/connsys_gnss_b.img'
    if hashlib.sha256(image.read_bytes()).hexdigest()!='633a1e2a19593d9cf12db4e140d893da9d1817c721381848831d90afc0b02463':
        raise RuntimeError('Shared firmware input does not match documented hash')
    return dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                exact_V2_10_repack_available=(ROOT/'release-runtime/public-b-assembled-root.tar').is_file(),
                build_mode='experimental full source rebuild; not original V2.10',
                original_release_links_recovered=False)


def prepare():
    info=check()
    WORK.mkdir(exist_ok=True)
    if (WORK/'prepared.json').exists():raise RuntimeError('Use a fresh checkout for a new source build')
    # Quarantine every old module and package DB before the first image build.
    # Only freshly compiled packages/custom modules can re-enter the overlay.
    for relative in ['lib/modules','lib/apk','usr/lib/lg6851f-vendor618/modules']:
        source=ROOT/'files'/relative
        if source.exists():
            destination=WORK/'original-overlay'/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(source,destination)
    for relative in ['usr/lib/libiwinfo.so.20230701','usr/lib/rpcd/luci.so','usr/sbin/lg6851f-hostapd']:
        source=ROOT/'files'/relative
        if source.exists():
            target=WORK/'original-overlay'/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(source,target)
    # HNAT and nf_nat snapshots are V2.10's actual source, not all historical
    # experiments applied one after another.
    shutil.copytree(ROOT/'development-history/V2.10/hwnat',ROOT/'package/vendor/mt6990-hwnat/src',dirs_exist_ok=True)
    wifi=ROOT/'vendor-source/wifi/mt_wifi_ap'
    shutil.copytree(REPAIRS,wifi/'cloud-repairs')
    with (wifi/'Makefile').open('a') as f:
        f.write('\n$(MOD_NAME)-objs += cloud-repairs/parser-repair.o cloud-repairs/action-repair.o cloud-repairs/pmksa-repair.o\n')
    rx=ROOT/'vendor-source/wifi/mt_wifi/ap/ap_data.c'
    if 'An authenticated STA may send a complete' not in rx.read_text():
        subprocess.run(['python3',str(ROOT/'development-history/V2.10/wifi-rebuild/patch-source.py'),str(rx)],check=True)
    cfg=ROOT/'vendor-source/wifi/mt_wifi/os/linux/cfg80211/cfg80211.c'
    source=cfg.read_text()
    old='(sizeof(u32)+sizeof(u32))*6, vendor_event, GFP_KERNEL)'
    if source.count(old)!=1:raise RuntimeError('Radar source context differs')
    cfg.write_text(source.replace(old,'(sizeof(u32)+sizeof(u32))*6, vendor_event - 1, GFP_KERNEL)'))
    config=ROOT/'.config'
    with config.open('a') as f:f.write('\nCONFIG_PACKAGE_libnl-core=y\nCONFIG_PACKAGE_libnl-genl=y\n')
    (WORK/'prepared.json').write_text(json.dumps(info,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    if args.check:print(json.dumps(check(),indent=2))
    else:prepare()
