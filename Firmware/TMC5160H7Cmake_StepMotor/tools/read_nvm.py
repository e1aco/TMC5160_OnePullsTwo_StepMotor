#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_nvm.py — 经 J-Link 读板载 BKPSRAM 测试留存（0x38800000，跨复位/掉电保持）

用法：python tools/read_nvm.py
可选：--jlink <JLink.exe> --device <型号> --out <bin>
说明：BKPSRAM 记录 = header[magic,count] + 每条 14 字(56B)：
      seq, boot, st_ok, ot_seen, legs[G1..G5], maxdev[G1..G5]
"""
import argparse
import glob
import os
import struct
import subprocess
import tempfile

NVM_ADDR = 0x38800000
NVM_SIZE = 0x1000           # 4KB
MAGIC = 0x4E564D32          # "NVM2"
HDR_WORDS = 4
REC_WORDS = 14
REC_MAX = (NVM_SIZE // 4 - HDR_WORDS) // REC_WORDS


def find_jlink():
    cands = glob.glob(r"C:\Program Files\SEGGER\JLink*\JLink.exe")
    return sorted(cands)[-1] if cands else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jlink", default=None)
    ap.add_argument("--device", default="STM32H750VB")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    jl = args.jlink or find_jlink()
    if not jl:
        print("未找到 JLink.exe，请用 --jlink 指定")
        return 2
    out = args.out or os.path.join(tempfile.gettempdir(), "nvm.bin")
    script = (f"si SWD\nspeed 1000\ndevice {args.device}\n"
              f"exec SetResetType = 0\nconnect\n"
              f"savebin {out},{hex(NVM_ADDR)},{hex(NVM_SIZE)}\nexit\n")
    sf = out + ".jlink"
    with open(sf, "w", encoding="ascii") as f:
        f.write(script)

    r = subprocess.run([jl, "-CommandFile", sf], capture_output=True, text=True)
    if not os.path.exists(out):
        print("读取失败：")
        print(r.stdout[-800:])
        return 1
    with open(out, "rb") as f:
        b = f.read()

    w = list(struct.unpack("<%dI" % (NVM_SIZE // 4), b[:NVM_SIZE]))
    if w[0] != MAGIC:
        print(f"魔数不符(0x{w[0]:08X})：BKPSRAM 未初始化或已掉电")
        return 1
    cnt = w[1]
    print(f"BKPSRAM @0x{NVM_ADDR:08X}  magic=ok  记录数={cnt}  (容量 {REC_MAX})")
    start = max(0, cnt - REC_MAX)
    for n in range(start, cnt):
        base = HDR_WORDS + (n % REC_MAX) * REC_WORDS
        seq, boot, st_ok, ot = w[base], w[base + 1], w[base + 2], w[base + 3]
        legs = w[base + 4: base + 9]
        md = struct.unpack("<5i", struct.pack("<5I", *w[base + 9: base + 14]))
        s = "  ".join(f"G{i+1}={legs[i]}/{md[i]}" for i in range(5))
        print(f"#{seq:<3} boot={boot:<3} selftest={'PASS' if st_ok else 'FAIL'} "
              f"ot={'YES' if ot else 'no'}  {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
