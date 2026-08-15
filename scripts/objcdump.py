#!/usr/bin/env python3
"""Dump ObjC class names, their ivars, and their method selectors from an arm64 Mach-O.

Walks __objc_classlist -> class_t -> class_ro_t -> {name, baseMethods, ivars}.
Handles both plain rebased pointers and DYLD_CHAINED_PTR_64 encodings.
"""
import struct, sys, json

MH_MAGIC_64 = 0xfeedfacf
LC_SEGMENT_64 = 0x19

def u64(b, o): return struct.unpack_from("<Q", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]

class Image:
    def __init__(self, path):
        self.data = open(path, "rb").read()
        assert u32(self.data, 0) == MH_MAGIC_64, "not arm64 Mach-O"
        self.segs = []       # (vmaddr, vmsize, fileoff)
        self.sects = {}      # (seg, sect) -> (addr, size, offset)
        ncmds = u32(self.data, 16)
        off = 32
        for _ in range(ncmds):
            cmd, cmdsize = u32(self.data, off), u32(self.data, off + 4)
            if cmd == LC_SEGMENT_64:
                segname = self.data[off+8:off+24].rstrip(b"\0").decode()
                vmaddr, vmsize, fileoff = u64(self.data, off+24), u64(self.data, off+32), u64(self.data, off+40)
                self.segs.append((vmaddr, vmsize, fileoff))
                nsects = u32(self.data, off+64)
                so = off + 72
                for _s in range(nsects):
                    sn = self.data[so:so+16].rstrip(b"\0").decode()
                    sg = self.data[so+16:so+32].rstrip(b"\0").decode()
                    self.sects[(sg, sn)] = (u64(self.data, so+32), u64(self.data, so+40), u32(self.data, so+48))
                    so += 80
            off += cmdsize
        # image base = vmaddr of __TEXT
        self.base = min(v for v, _, _ in self.segs if v)

    def off_for(self, vmaddr):
        for v, sz, fo in self.segs:
            if v <= vmaddr < v + sz:
                return fo + (vmaddr - v)
        return None

    def ptr(self, vmaddr):
        """Read a pointer, decoding chained-fixup rebase encodings."""
        o = self.off_for(vmaddr)
        if o is None: return 0
        raw = u64(self.data, o)
        if raw == 0: return 0
        if self.mapped(raw):
            return raw
        # DYLD_CHAINED_PTR_64 rebase: target lives in the low 36 bits, either as a full
        # unslid vmaddr or as an offset from the image base.
        target = raw & 0xFFFFFFFFF
        if self.mapped(target):
            return target
        if self.mapped(self.base + target):
            return self.base + target
        return 0

    def mapped(self, vmaddr):
        return any(v <= vmaddr < v + sz for v, sz, fo in self.segs if fo or v == self.base)

    def cstr(self, vmaddr):
        o = self.off_for(vmaddr)
        if o is None: return None
        end = self.data.find(b"\0", o)
        if end < 0 or end - o > 512: return None
        try: return self.data[o:end].decode()
        except UnicodeDecodeError: return None

    def classes(self):
        key = ("__DATA_CONST", "__objc_classlist")
        if key not in self.sects: key = ("__DATA", "__objc_classlist")
        addr, size, _ = self.sects[key]
        out = {}
        for i in range(size // 8):
            cls = self.ptr(addr + i * 8)
            if not cls: continue
            info = self.read_class(cls)
            if info: out[info[0]] = info[1]
        return out

    def read_class(self, cls):
        data_ptr = self.ptr(cls + 32)
        ro = data_ptr & ~0x7
        if not ro: return None
        name = self.cstr(self.ptr(ro + 24))
        if not name: return None
        ivars, sels = [], []
        iv = self.ptr(ro + 48)
        if iv:
            o = self.off_for(iv)
            if o is not None:
                entsize, count = u32(self.data, o), u32(self.data, o + 4)
                if entsize == 32 and count < 4096:
                    for j in range(count):
                        n = self.cstr(self.ptr(iv + 8 + j * 32 + 8))
                        if n: ivars.append(n)
        mp = self.ptr(ro + 32)
        if mp:
            o = self.off_for(mp)
            if o is not None:
                entsize, count = u32(self.data, o), u32(self.data, o + 4)
                small = bool(entsize & 0x80000000)
                esz = entsize & 0xFFFC
                if esz in (12, 24) and count < 8192:
                    for j in range(count):
                        eo = o + 8 + j * esz
                        ea = mp + 8 + j * esz
                        if small:
                            rel = struct.unpack_from("<i", self.data, eo)[0]
                            nameref = ea + rel
                            n = self.cstr(self.ptr(nameref))
                        else:
                            n = self.cstr(self.ptr(ea))
                        if n: sels.append(n)
        return name, {"ivars": ivars, "methods": sels}

if __name__ == "__main__":
    img = Image(sys.argv[1])
    cls = img.classes()
    json.dump(cls, open(sys.argv[2], "w"))
    print(f"{sys.argv[1]}: {len(cls)} classes, "
          f"{sum(len(v['ivars']) for v in cls.values())} ivars, "
          f"{sum(len(v['methods']) for v in cls.values())} methods")
