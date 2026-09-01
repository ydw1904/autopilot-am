#!/usr/bin/env python3
"""Dump the AM app's C# types, fields and method signatures from the APK.

This is how the schedule-write endpoints were recovered (ticket 013, gap 1)
without a mitmproxy capture: `global-metadata.dat` carries il2cpp's
type/field/method/**parameter** tables in the clear, and on this API the
parameter names of the `Api.*Calls` methods ARE the form field names. Grepping
`strings` over the same file only gets you one alphabetised run with no
structure.

    unzip -o apk/*.apkm -d /tmp/apkm
    unzip -o /tmp/apkm/base.apk -d /tmp/base 'assets/bin/Data/Managed/Metadata/*'
    ./il2cpp_meta.py /tmp/base/assets/bin/Data/Managed/Metadata/global-metadata.dat Planning

Metadata version 31 only; it prints the version and bails otherwise, because
the table strides move between versions and a silent misparse looks like data.
"""

import struct
import sys

# Header at offset 8 is 31 (offset, size) int32 pairs. The ones used here, by
# pair index, with their entry strides.
STRINGS, METHODS, PARAMS, FIELDS, TYPEDEFS = 2, 5, 10, 11, 19
STRIDE = {METHODS: 36, PARAMS: 12, FIELDS: 12, TYPEDEFS: 88}


class Metadata:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        sanity, version = struct.unpack("<Ii", self.d[:8])
        if sanity != 0xFAB11BAF:
            sys.exit(f"{path}: not an il2cpp global-metadata.dat")
        if version != 31:
            sys.exit(f"metadata version {version}, this parser only knows 31")
        v = struct.unpack("<62i", self.d[8:256])
        self.at = {i: (v[2 * i], v[2 * i + 1]) for i in range(31)}

    def _rec(self, table, index, fmt):
        off = self.at[table][0] + index * STRIDE[table]
        return struct.unpack(fmt, self.d[off:off + STRIDE[table]])

    def count(self, table):
        return self.at[table][1] // STRIDE[table]

    def s(self, index):
        base = self.at[STRINGS][0] + index
        return self.d[base:self.d.index(b"\0", base)].decode("utf-8", "replace")

    def field(self, i):
        return self.s(self._rec(FIELDS, i, "<3i")[0])

    def param(self, i):
        return self.s(self._rec(PARAMS, i, "<3i")[0])

    def method(self, i):
        f = self._rec(METHODS, i, "<7i4h")
        params = [self.param(f[4] + k) for k in range(max(0, f[10]))]
        return f"{self.s(f[0])}({', '.join(params)})"

    def type_def(self, i):
        f = self._rec(TYPEDEFS, i, "<16i8H2I")
        return {
            "name": self.s(f[0]), "namespace": self.s(f[1]),
            "fields": [self.field(f[8] + j) for j in range(f[18])],
            "methods": [self.method(f[9] + j) for j in range(f[16])],
        }


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    md = Metadata(sys.argv[1])
    needle = sys.argv[2].lower()
    for i in range(md.count(TYPEDEFS)):
        t = md.type_def(i)
        full = f"{t['namespace']}.{t['name']}".lstrip(".")
        if needle not in full.lower():
            continue
        print(f"=== {full}")
        for f in t["fields"]:
            print(f"    field  {f}")
        for m in t["methods"]:
            print(f"    method {m}")


if __name__ == "__main__":
    main()
