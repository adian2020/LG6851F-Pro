"""Small read-only ELF64/AArch64 reader for the repository's module checks."""
import struct
from pathlib import Path


class Elf:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        if self.data[:6] != b'\x7fELF\x02\x01':
            raise ValueError('Expected little-endian ELF64')
        header = struct.unpack_from('<16sHHIQQQIHHHHHH', self.data)
        if header[2] != 183 or header[11] != 64:
            raise ValueError('Expected AArch64 ELF section table')
        self.sh = [struct.unpack_from('<IIQQQQIIQQ', self.data, header[6]+i*64) for i in range(header[12])]
        strings = self.bytes(header[13])
        self.names = [self.string(strings, s[0]) for s in self.sh]
        table = self.names.index('.symtab')
        names = self.bytes(self.sh[table][6])
        raw = self.bytes(table)
        self.symbols = []
        for offset in range(0, len(raw), 24):
            name, info, other, section, value, size = struct.unpack_from('<IBBHQQ', raw, offset)
            self.symbols.append(dict(name=self.string(names,name), info=info, other=other,
                                     section=section, value=value, size=size,index=offset//24))

    @staticmethod
    def string(data, offset):
        return data[offset:data.index(b'\0',offset)].decode()

    def bytes(self, section):
        row=self.sh[section]
        return self.data[row[4]:row[4]+row[5]]

    def functions(self):
        return {s['name']:s for s in self.symbols if s['info'] & 15 == 2 and s['section'] and s['size']}

    def symbytes(self, symbol):
        start=self.sh[symbol['section']][4]+symbol['value']
        return self.data[start:start+symbol['size']]
