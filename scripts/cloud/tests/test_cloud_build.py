import importlib.util
import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/cloud'))
from elf64 import Elf
import prepare_source
import assemble
from vendor_repair import repair


class CloudBuildTest(unittest.TestCase):
    def test_original_release_runtime_is_not_silently_required(self):
        info=prepare_source.check()
        self.assertIn('experimental',info['build_mode'])
        self.assertFalse(info['original_release_links_recovered'])

    def test_vendor_preflight_finds_missing_literal_and_copy_inputs(self):
        with tempfile.TemporaryDirectory() as name, patch.object(prepare_source, 'ROOT', Path(name)):
            package = Path(name) / 'package/vendor/dashboard'
            payload = package / 'files'
            payload.mkdir(parents=True)
            (package / 'Makefile').write_text(
                '\t$(INSTALL_DATA) ./files/menu.json $(1)/menu.json\n'
                '\t$(CP) ./files/assets/* $(1)/\n'
                '\t$(CP) $(CURDIR)/root $(PKG_BUILD_DIR)\n'
                '\t$(INSTALL_BIN) $(TOPDIR)/files/etc/init.d/openclash $(1)/etc/init.d/\n'
                '\t$(INSTALL_BIN) $(PKG_BUILD_DIR)/generated $(1)/bin/\n'
            )
            self.assertEqual(prepare_source.missing_vendor_payloads(), [
                'package/vendor/dashboard/files/menu.json',
                'package/vendor/dashboard/files/assets/*',
                'package/vendor/dashboard/root',
                'files/etc/init.d/openclash',
            ])
            (payload / 'menu.json').touch()
            (payload / 'assets').mkdir()
            (payload / 'assets/home.js').touch()
            (package / 'root').mkdir()
            init = Path(name) / 'files/etc/init.d/openclash'
            init.parent.mkdir(parents=True)
            init.touch()
            self.assertEqual(prepare_source.missing_vendor_payloads(), [])

    def test_elf_reader_rejects_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'elf'
            data=bytearray(64);data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',data,18,62)
            path.write_bytes(data)
            with self.assertRaises(ValueError):Elf(path)

    def test_module_identity_detects_changed_code(self):
        original=ROOT/'files/usr/lib/lg6851f-vendor618/modules/mt_wifi_cmn.ko'
        elf=Elf(original);data=bytearray(elf.data)
        data[elf.sh[elf.names.index('.text')][4]]^=1
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'changed.ko';path.write_bytes(data)
            self.assertNotEqual(assemble.identity(original),assemble.identity(path))

    def test_preparation_quarantines_old_modules_and_database(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for relative in ['files/lib/modules/6.18.44/old.ko','files/lib/apk/db/installed',
                             'files/usr/lib/lg6851f-vendor618/modules/old.ko',
                             'vendor-source/wifi/mt_wifi/ap/ap_data.c',
                             'vendor-source/wifi/mt_wifi/os/linux/cfg80211/cfg80211.c',
                             'vendor-source/wifi/mt_wifi_ap/Makefile','.config',
                             'development-history/V2.10/hwnat/Makefile']:
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            (root/'vendor-source/wifi/mt_wifi/ap/ap_data.c').write_text('An authenticated STA may send a complete')
            (root/'vendor-source/wifi/mt_wifi/os/linux/cfg80211/cfg80211.c').write_text('(sizeof(u32)+sizeof(u32))*6, vendor_event, GFP_KERNEL)')
            repairs=root/'repair-input';repairs.mkdir()
            for filename in ['parser-repair.c','pmksa-repair.c','action-repair.c']:(repairs/filename).write_text('source')
            with patch.object(prepare_source,'ROOT',root),patch.object(prepare_source,'WORK',root/'build-cloud'),patch.object(prepare_source,'REPAIRS',repairs),patch.object(prepare_source,'check',return_value={'mode':'fixture'}):
                prepare_source.prepare()
                self.assertFalse((root/'files/lib/modules').exists())
                self.assertFalse((root/'files/lib/apk').exists())
                self.assertEqual((root/'build-cloud/original-overlay/lib/apk/db/installed').read_text(),'fixture')
                with self.assertRaises(RuntimeError):prepare_source.prepare()

    def test_slot_overlay_cannot_replace_fresh_apk_database_or_reuse_test_claims(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for relative,content in [('files/lib/apk/db/installed','fresh-db'),('files-slots/b/lib/apk/db/installed','old-db'),
                                     ('files-slots/b/etc/lg6851f-build.json',json.dumps({'physical_boot_tested':True,'functional_tests_run':True}))]:
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
            with patch.object(assemble,'ROOT',root),patch.object(assemble.subprocess,'check_output',return_value='fixture-sha\n'):
                assemble.slot('b','fixture-build','adian2020/LG6851F-Pro')
            self.assertEqual((root/'files/lib/apk/db/installed').read_text(),'fresh-db')
            data=json.loads((root/'files/etc/lg6851f-build.json').read_text())
            self.assertFalse(data['physical_boot_tested']);self.assertFalse(data['functional_tests_run'])
            self.assertFalse(data['original_V2_10_reproduction'])

    def test_database_validation_uses_this_builds_pre_overlay_installation(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name)
            database=Path('lib/apk/db/installed')
            for directory in ('root.orig-mediatek','root-mediatek'):
                path=parent/directory/database
                path.parent.mkdir(parents=True)
                path.write_text('fresh-db')
            root=parent/'root-mediatek'
            assemble.verify_package_database(root)
            (root/database).write_text('old-db')
            with self.assertRaisesRegex(RuntimeError,'fresh package installation'):
                assemble.verify_package_database(root)

    def test_relocation_repair_roundtrip_on_real_aarch64_module(self):
        original=ROOT/'files/usr/lib/lg6851f-vendor618/modules/mt_wifi.ko'
        elf=Elf(original);functions=elf.functions()
        manifest=json.loads((ROOT/'development-history/V2.10/wifi-rebuild/driver-repair.json').read_text())
        data=bytearray(elf.data);text=elf.names.index('.text');rela=elf.names.index('.rela.text')
        entries={functions[row['original_symbol']]['value']:row for row in manifest['patched_entries']}
        rows=[]
        wiphy=next(s['index'] for s in elf.symbols if s['name']=='wiphy_register')
        relocation_bytes=elf.bytes(rela)
        for offset in range(0,len(relocation_bytes),24):
            row=list(struct.unpack_from('<QQq',relocation_bytes,offset))
            if row[0] in entries and row[1]&0xffffffff==282:continue
            if elf.symbols[row[1]>>32]['name']=='lg9_wiphy_register':row[1]=wiphy<<32|283
            rows.append(row)
        for offset,row in entries.items():data[elf.sh[text][4]+offset:elf.sh[text][4]+offset+4]=bytes.fromhex(row['instruction_before'])
        client=functions['CFG80211DRV_OpsVifAdd'];client_offset=elf.sh[text][4]+client['value']+0x220
        struct.pack_into('<I',data,client_offset,0x540003e2)
        while len(data)%8:data.append(0)
        start=len(data)
        for row in rows:data.extend(struct.pack('<QQq',*row))
        header=struct.unpack_from('<16sHHIQQQIHHHHHH',data)
        struct.pack_into('<Q',data,header[6]+rela*64+24,start);struct.pack_into('<Q',data,header[6]+rela*64+32,len(rows)*24)
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'fresh-layout.ko';path.write_bytes(data)
            result=repair(path)
            self.assertEqual(len(result['entries']),7)
            after=Elf(path)
            self.assertEqual(after.bytes(text),elf.bytes(text))
            self.assertTrue(result['metadata_unchanged'])
            with self.assertRaises(ValueError):repair(path)


if __name__=='__main__':unittest.main()
