#!/usr/bin/env python3
"""Apply the documented entry relocations to a freshly compiled module.

Repair C objects are linked by Kbuild, using current headers. No old .o/.ko is
merged and no missing symbol or changed instruction is silently accepted.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path
from elf64 import Elf


def repair(path):
    before=Elf(path)
    functions=before.functions()
    targets={
        'defrag_multi_link_per_sta_profile':'lg6_defrag_multi_link_per_sta_profile',
        'parse_multi_link_per_sta_profile':'lg6_parse_multi_link_per_sta_profile',
        'parse_multi_link_ie':'lg6_parse_multi_link_ie',
        'PeerProtectEHTAction':'lg6_PeerProtectEHTAction',
        'CFG80211_OpsPmksaSet':'lg9_pmksa_set',
        'CFG80211_OpsPmksaFlush':'lg9_pmksa_flush',
        'get_rate_flag':'lg9_get_rate_flag',
    }
    text=before.names.index('.text')
    rela=before.names.index('.rela.text')
    relocation_bytes=before.bytes(rela)
    relocations=[list(struct.unpack_from('<QQq',relocation_bytes,n)) for n in range(0,len(relocation_bytes),24)]
    data=bytearray(before.data)
    changes=[]
    for original,replacement in targets.items():
        a,b=functions[original],functions[replacement]
        if a['section']!=text or not before.sh[b['section']][2]&4 or a['value']%4:
            raise ValueError('Unexpected function layout: '+original)
        offset=before.sh[text][4]+a['value']
        instruction=struct.unpack_from('<I',data,offset)[0]
        if instruction==0x14000000 or any(a['value']<=r[0]<a['value']+4 for r in relocations):
            raise ValueError('Entry already patched or relocated: '+original)
        struct.pack_into('<I',data,offset,0x14000000)
        relocations.append([a['value'],b['index']<<32|282,0])
        changes.append(dict(original=original,replacement=replacement,instruction_before=hex(instruction)))
    wrapper=functions['lg9_wiphy_register']
    calls=[r for r in relocations if before.symbols[r[1]>>32]['name']=='wiphy_register' and
           not wrapper['value']<=r[0]<wrapper['value']+wrapper['size']]
    if len(calls)!=1 or calls[0][1]&0xffffffff!=283:
        raise ValueError('Expected one original wiphy registration call')
    calls[0][1]=wrapper['index']<<32|283
    # Source uses the checked vendor-event index correction. This last guard is
    # the documented APCLI condition repair, with an exact instruction check.
    client=functions['CFG80211DRV_OpsVifAdd']
    client_offset=before.sh[client['section']][4]+client['value']+0x220
    if struct.unpack_from('<I',data,client_offset)[0]!=0x540003e2:
        raise ValueError('APCLI guard instruction differs; inspect new disassembly')
    struct.pack_into('<I',data,client_offset,0x540003ea)
    while len(data)%8:data.append(0)
    offset=len(data)
    for row in sorted(relocations):data.extend(struct.pack('<QQq',*row))
    header=struct.unpack_from('<16sHHIQQQIHHHHHH',data)
    struct.pack_into('<Q',data,header[6]+rela*64+24,offset)
    struct.pack_into('<Q',data,header[6]+rela*64+32,len(relocations)*24)
    out=Path(str(path)+'.repaired')
    out.write_bytes(data)
    after=Elf(out)
    after_functions=after.functions()
    if before.bytes(before.names.index('.modinfo'))!=after.bytes(after.names.index('.modinfo')):
        raise ValueError('Unexpected module metadata change')
    for name,symbol in functions.items():
        old,new=before.symbytes(symbol),after.symbytes(after_functions[name])
        if name in targets:
            if old[4:]!=new[4:]:raise ValueError('Unexpected entry body change: '+name)
        elif name=='CFG80211DRV_OpsVifAdd':
            if old[:0x220]!=new[:0x220] or old[0x224:]!=new[0x224:]:raise ValueError('APCLI body changed')
        elif old!=new:raise ValueError('Unexpected function change: '+name)
    out.replace(path)
    return dict(source_sha256=hashlib.sha256(before.data).hexdigest(),output_sha256=hashlib.sha256(data).hexdigest(),entries=changes,
                repair_source='development-history/systemfix-source/systemfix-20261021-30/driver-build/repair-source',
                metadata_unchanged=True,hardware_tested=False)


if __name__=='__main__':
    result=repair(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(result,indent=2)+'\n')
