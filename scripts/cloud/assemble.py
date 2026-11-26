#!/usr/bin/env python3
"""Slot overlays, fresh hostapd, matched APK feed and output verification."""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from elf64 import Elf

ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'build-cloud'


def identity(path):
    elf=Elf(path)
    sections=[]
    for i,row in enumerate(elf.sh):
        name=elf.names[i]
        if row[2]&2 and not name.startswith('.note'):
            sections.append((name,row[5] if row[1]==8 else hashlib.sha256(elf.bytes(i)).hexdigest()))
        if row[1]==4 and elf.sh[row[7]][2]&2:
            relocations=[];raw=elf.bytes(i)
            for offset in range(0,len(raw),24):
                address,info,addend=__import__('struct').unpack_from('<QQq',raw,offset)
                symbol=elf.symbols[info>>32];index=symbol['section']
                section=elf.names[index] if index<len(elf.names) else str(index)
                relocations.append((address,info&0xffffffff,symbol['name'],section,symbol['value'],addend))
            sections.append((name,relocations))
    return hashlib.sha256(json.dumps(sections).encode()).hexdigest()


def hostapd(jobs):
    compiler=next(ROOT.glob('staging_dir/toolchain-aarch64_cortex-a55*/bin/aarch64-openwrt-linux-musl-gcc'))
    target=next(ROOT.glob('staging_dir/target-aarch64_cortex-a55*'))
    tree=ROOT/'development-history/V2.10/hostapd-native/hostapd'
    vendor_headers=ROOT/'vendor-include/mtk_nl80211_inc'
    supplicant_headers=tree.parent/'wpa_supplicant'
    env=dict(os.environ,STAGING_DIR=str(target),PKG_CONFIG_LIBDIR=str(target/'usr/lib/pkgconfig'),PKG_CONFIG_SYSROOT_DIR=str(target),
             LDFLAGS='-L'+str(target/'usr/lib')+' -Wl,-rpath-link,'+str(target/'lib')+' -Wl,-rpath-link,'+str(target/'usr/lib'))
    subprocess.run(['make','-C',str(tree),'-j'+jobs,'CC='+str(compiler),'CONFIG_LIBNL32=y',
                    'CONFIG_TLS=openssl',
                    'LIBNL_INC='+str(target/'usr/include/libnl3'),
                    f'EXTRA_CFLAGS=-I{target / "usr/include"} -I{vendor_headers} -I{supplicant_headers}',
                    'hostapd'],env=env,check=True)
    out=ROOT/'files/usr/sbin/lg6851f-hostapd'
    shutil.copyfile(tree/'hostapd',out);out.chmod(0o755)
    subprocess.run([str(compiler)[:-3]+'strip','--strip-debug',str(out)],check=True)


def slot(name,build_id,repo):
    for source in (ROOT/'files-slots'/name).rglob('*'):
        relative=source.relative_to(ROOT/'files-slots'/name)
        if str(relative).startswith('lib/apk'):continue
        target=ROOT/'files'/relative
        if source.is_file():
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    metadata=ROOT/'files/etc/lg6851f-build.json'
    data=json.loads(metadata.read_text())
    for key in data:
        if key.endswith(('_tested','_verified','tests_run','checks_run')):data[key]=False
    data.update(version='source-experimental-'+build_id,built_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                base='fork-source-'+subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                driver_build='fresh source and current headers; checked repair relocations',target_slot=name,
                source_experimental=True,original_V2_10_reproduction=False)
    metadata.write_text(json.dumps(data,indent=2)+'\n')
    feeds=ROOT/'files/etc/apk/repositories.d'
    feeds.mkdir(parents=True,exist_ok=True)
    for path in feeds.glob('*.list'):path.write_text('# Dedicated source-build feed is defined by distfeeds.list.\n')
    urls='\n'.join('https://raw.githubusercontent.com/'+repo+'/cloud-build-lg6851f-pro-apk/'+build_id+'/'+feed+'/packages.adb' for feed in ['target','base','luci','packages'])+'\n'
    (feeds/'distfeeds.list').write_text(urls)
    defaults=ROOT/'files/etc/uci-defaults/98-lg6851f-apk-compatible-feeds'
    defaults.parent.mkdir(parents=True,exist_ok=True)
    defaults.write_text("#!/bin/sh\nset -eu\ncat > /etc/apk/repositories.d/distfeeds.list <<'FEEDS'\n"+urls+'FEEDS\nexit 0\n');defaults.chmod(0o755)
    (ROOT/'files/etc/lg6851f-source-build-id').write_text(build_id+'\n')


def verify_root(name):
    root=next(ROOT.glob('build_dir/target-*/root-mediatek'))
    if (root/'private-key.pem').exists():raise RuntimeError('Private APK key in rootfs')
    start=(root/'usr/sbin/lg6851f-vendor618-start').read_text()
    platform=(root/'lib/upgrade/platform.sh').read_text()
    partition='mmcblk0p26' if name=='a' else 'mmcblk0p39'
    if 'bootslot='+name not in start or partition not in platform:raise RuntimeError('Wrong slot runtime gate')
    verify_package_database(root)
    providers={}
    folders=list(ROOT.glob('build_dir/target-*/linux-mediatek_mt6990'))+[ROOT/'vendor-source',ROOT/'build-public-modules']
    for folder in folders:
        for path in folder.rglob('*.ko'):
            if any(part.startswith(('root-','ipkg-','.pkgdir')) for part in path.parts):continue
            providers.setdefault(path.name,set()).add(identity(path))
    modules=[]
    for path in root.rglob('*.ko'):
        if identity(path) not in providers.get(path.name,set()):raise RuntimeError('Module has no fresh build provenance: '+str(path))
        elf=Elf(path)
        metadata=elf.bytes(elf.names.index('.modinfo'))
        if b'vermagic=6.18.44 ' not in metadata:raise RuntimeError('Wrong module kernel: '+str(path))
        modules.append(str(path.relative_to(root)))
    required=['mt_wifi.ko','mt_wifi_cmn.ko','mtk_warp.ko','mtk_hwifi.ko','wifi_md_coex.ko','hwnat.ko',
              'connac_if.ko','mtk_pci.ko','mtk_wed.ko','mt7990.ko','mt7991.ko',
              'lg6851f_cpufreq_hw.ko','lg6851f_panel_leds.ko','lg6851f_ppe_stats.ko']
    if not set(required)<=set(Path(p).name for p in modules):raise RuntimeError('Required rebuilt modules missing')
    result=dict(slot=name,module_count=len(modules),fresh_module_provenance=True,physical_boot_tested=False,
                root=str(root),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
    (WORK/('root-check-'+name+'.json')).write_text(json.dumps(result,indent=2)+'\n')


def verify_package_database(root):
    database=Path('lib/apk/db/installed')
    # OpenWrt saves the result of this run's APK install before merging files/.
    installed_root=root.with_name('root.orig-mediatek')
    fresh=(installed_root/database).read_bytes()
    if not fresh or (root/database).read_bytes()!=fresh:
        raise RuntimeError('Rootfs APK database does not match the fresh package installation')


def export(name):
    slot_dir=ROOT/('A槽单刷boot和rootfs签名成功包' if name=='a' else 'B槽单刷boot和rootfs签名成功包')
    package=ROOT/'bin/targets/mediatek/mt6990'/('lg6851f-mt6990-'+name+'-signed-sysupgrade.tar.gz')
    with tarfile.open(package,'r:gz') as tar:
        if tar.getnames()[0]!='CONTROL':raise RuntimeError('CONTROL is not first')
        control=tar.extractfile('CONTROL').read().decode()
        if '\nslot='+name+'\n' not in '\n'+control:raise RuntimeError('Wrong package slot')
        sums=tar.extractfile('SHA256SUMS').read().decode()
        for line in sums.splitlines():
            expected,filename=line.split()
            actual=hashlib.sha256(tar.extractfile(filename).read()).hexdigest()
            if actual!=expected:raise RuntimeError('Bad packaged hash: '+filename)
    target=ROOT/'cloud-dist'/name;target.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(package,target/package.name)
    for filename in ['boot_'+name+'.img','rootfs_'+name+'.img','lg6851f-signed-boot-rootfs.manifest.json']:
        shutil.copyfile(slot_dir/filename,target/filename)
    shutil.copyfile(WORK/('root-check-'+name+'.json'),target/'root-check.json')


def feeds(build_id):
    out=ROOT/'build-cloud/compiled-feed';out.mkdir(parents=True,exist_ok=True)
    arch='aarch64_cortex-a55_neon-vfpv4'
    sources={'target':ROOT/'bin/targets/mediatek/mt6990/packages'}
    sources.update({name:ROOT/'bin/packages'/arch/name for name in ['base','luci','packages']})
    keys=WORK/'verify-keys';keys.mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'public-key.pem',keys/'public-key.pem')
    apk=ROOT/'staging_dir/host/bin/apk'
    for name,source in sources.items():
        subprocess.run([str(apk),'--keys-dir',str(keys),'verify',str(source/'packages.adb')],check=True)
        target=out/name;target.mkdir(exist_ok=True)
        for path in source.iterdir():
            if path.name=='packages.adb' or path.suffix=='.apk':shutil.copyfile(path,target/path.name)
    shutil.copyfile(ROOT/'public-key.pem',out/'public-key.pem')
    (out/'build-id.txt').write_text(build_id+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('step',choices=['hostapd','slot','verify','export','feeds'])
    parser.add_argument('--slot',choices=['a','b']);parser.add_argument('--jobs',default='4');parser.add_argument('--build-id');parser.add_argument('--repo');args=parser.parse_args()
    if args.step=='hostapd':hostapd(args.jobs)
    elif args.step=='slot':slot(args.slot,args.build_id,args.repo)
    elif args.step=='verify':verify_root(args.slot)
    elif args.step=='export':export(args.slot)
    else:feeds(args.build_id)
