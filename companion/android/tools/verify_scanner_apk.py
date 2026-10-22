#!/usr/bin/env python3
"""Check actual DEX class definitions, not strings that only reference a missing class."""
from pathlib import Path
import argparse
import struct
import zipfile

REQUIRED = (
    'Lcom/journeyapps/barcodescanner/CaptureActivity;',
    'Lcom/journeyapps/barcodescanner/CaptureManager;',
    'Landroidx/core/content/ContextCompat;',
    'Landroidx/core/app/ActivityCompat;',
    'Landroidx/fragment/app/Fragment;',
    'Landroidx/activity/result/contract/ActivityResultContract;',
)

def definitions(data):
    if not data.startswith(b'dex\n') or len(data) < 112:
        raise ValueError('Invalid DEX header')
    def u32(offset):
        if offset < 0 or offset + 4 > len(data):
            raise ValueError('DEX offset out of range')
        return struct.unpack_from('<I', data, offset)[0]
    if u32(40) != 0x12345678:
        raise ValueError('Unsupported DEX byte order')
    string_count, string_offset = u32(56), u32(60)
    type_count, type_offset = u32(64), u32(68)
    class_count, class_offset = u32(96), u32(100)
    result = set()
    for i in range(class_count):
        type_index = u32(class_offset + 32*i)
        if type_index >= type_count:
            raise ValueError('Invalid class type index')
        string_index = u32(type_offset + 4*type_index)
        if string_index >= string_count:
            raise ValueError('Invalid descriptor string index')
        offset = u32(string_offset + 4*string_index)
        # Skip the descriptor's ULEB128 UTF-16 length.
        for _ in range(5):
            if offset >= len(data):
                raise ValueError('Invalid descriptor length')
            byte = data[offset]
            offset += 1
            if byte < 128:
                break
        else:
            raise ValueError('Invalid ULEB128 length')
        end = data.find(b'\0', offset)
        if end < 0:
            raise ValueError('Unterminated class descriptor')
        result.add(data[offset:end].decode('utf-8'))
    return result

def verify(path):
    classes = set()
    with zipfile.ZipFile(path) as apk:
        for name in apk.namelist():
            if name.startswith('classes') and name.endswith('.dex') and '/' not in name:
                classes.update(definitions(apk.read(name)))
    missing = [name for name in REQUIRED if name not in classes]
    if missing:
        raise ValueError(path.name+' is missing scanner runtime definitions: '+', '.join(missing))
    print(path.name+': scanner DEX runtime definitions PASS')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    paths = sorted(args.path.glob('*.apk')) if args.path.is_dir() else [args.path]
    if not paths:
        raise SystemExit('No APKs to verify')
    for path in paths:
        verify(path)

if __name__ == '__main__':
    main()
