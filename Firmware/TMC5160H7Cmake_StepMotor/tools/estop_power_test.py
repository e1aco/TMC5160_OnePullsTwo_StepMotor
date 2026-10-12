#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""estop_power_test.py — 急停"物理断功率"验证（H 级：需人工观察电机）

验证 v2.1 急停语义（require.md 附录 A）：
  1) 速度模式(0x04)转动中发 0x07/value=0xFF，且 **掩码非法(0x00)** → 仍执行、关断全部电机
  2) 反馈 0xE1（未校准）；此后运动命令被拒(0xE1)
  3) 0x09 可读位置（豁免）→ 观察断电后位置滑行剖面

判据（M 部分，脚本自动判）：
  - VEL 后 0x09 位置确有变化（电机在动）           → 前置成立
  - ESTOP(mask=0x00) 回 0xE1                       → 全关语义成立
  - ESTOP 后 VEL 再发 → 0xE1（被拒）               → 门控恢复
判据（H 部分，需你确认）：电机断电后**自由滑停/可手动转动**（ENN 拉高、无保持力）。

用法：python tools/estop_power_test.py [--param 1] [--spin 1.2]
"""
import sys
import time

import can

CMD_ID = 0x1AA55F42
FB_ID = 0x1AA55F43
CMD_VEL = 0x04
CMD_STOP = 0x07
CMD_READ_POS = 0x09
M_U1, M_U2 = 0x01, 0x02
FLAG_ON = 0xFF
ERRS = {0xE1, 0xE2, 0xE3, 0xE4}


def build(value, cmd, motor, param=0):
    u = int(value) & 0xFFFFFFFF
    d = [cmd, motor, param,
         (u >> 24) & 0xFF, (u >> 16) & 0xFF, (u >> 8) & 0xFF, u & 0xFF]
    d.append(sum(d) & 0xFF)
    return bytearray(d)


def parse(d):
    if len(d) != 8 or (sum(d[:7]) & 0xFF) != d[7]:
        return None
    if d[0] == d[1] == d[2] == d[3] == d[4] == d[5] == d[6] and d[0] in ERRS:
        return {"kind": "error", "code": d[0]}
    if d[0] == CMD_READ_POS:
        return {"kind": "pos", "motor": d[1], "status": d[2],
                "pos": int.from_bytes(d[3:7], "big", signed=True)}
    if d[1] == 0 and d[2] == 0 and d[3] == 0 and d[4] == 0 and d[5] == 0 and d[6] == 0:
        return {"kind": "ack", "cmd": d[0]}
    return {"kind": "arr", "cmd": d[0], "motor": d[1], "status": d[6]}


class Bus:
    def __init__(self):
        self.b = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
        while self.b.recv(timeout=0.2) is not None:
            pass

    def send(self, value, cmd, motor, param=0):
        self.b.send(can.Message(arbitration_id=CMD_ID, is_extended_id=True,
                                data=build(value, cmd, motor, param)))

    def collect(self, sec):
        got = []
        end = time.monotonic() + sec
        while time.monotonic() < end:
            m = self.b.recv(timeout=max(0.0, end - time.monotonic()))
            if m is None:
                continue
            if m.arbitration_id != FB_ID:
                continue
            r = parse(bytes(m.data))
            if r:
                got.append(r)
        return got

    def read_pos(self, motor, sec=0.6):
        self.send(0, CMD_READ_POS, motor)
        for r in self.collect(sec):
            if r["kind"] == "pos" and r["motor"] == motor:
                return r["pos"]
        return None

    def shutdown(self):
        self.b.shutdown()


def main():
    param = 1
    spin = 1.2
    for a in sys.argv[1:]:
        if a.startswith("--param="):
            param = int(a.split("=")[1])
        elif a.startswith("--spin="):
            spin = float(a.split("=")[1])
    ok = True
    b = Bus()
    try:
        # 前置：停稳（若未校准 → 0xE1，提示先复位）
        b.send(0, CMD_STOP, M_U1)
        r = b.collect(0.5)
        if any(x["kind"] == "error" and x["code"] == 0xE1 for x in r):
            print("[FAIL] 板端未校准，请先复位(HOME_ZeroAtPowerUp)后重试")
            return 1
        # 1) 速度模式转动
        b.send(FLAG_ON, CMD_VEL, M_U1, param)   # param 组，0xFF=反转
        ack = any(x["kind"] == "ack" and x["cmd"] == CMD_VEL for x in b.collect(0.6))
        p0 = b.read_pos(M_U1)
        time.sleep(spin)
        p1 = b.read_pos(M_U1)
        moving = (p0 is not None and p1 is not None and abs(p1 - p0) > 512)
        print("[%s] VEL 转动  p0=%s p1=%s Δ=%s"
              % ("PASS" if (ack and moving) else "FAIL", p0, p1,
                 (p1 - p0) if (p0 is not None and p1 is not None) else None))
        ok = ok and ack and moving

        # 2) 急停：掩码非法 0x00 → 仍全关
        b.send(FLAG_ON, CMD_STOP, 0x00)
        rt = b.collect(0.8)
        estop_ok = any(x["kind"] == "error" and x["code"] == 0xE1 for x in rt)
        print("[%s] ESTOP mask=0x00 → 0xE1（全关语义）" % ("PASS" if estop_ok else "FAIL"))
        ok = ok and estop_ok

        # 3) 滑行剖面：断电后位置应渐停
        prof = []
        for _ in range(8):
            p = b.read_pos(M_U1, sec=0.3)
            prof.append(p)
            time.sleep(0.15)
        print("      断电后位置剖面: %s" % prof)

        # 4) 运动被拒
        b.send(FLAG_ON, CMD_VEL, M_U1, param)
        rt = b.collect(0.8)
        rej = any(x["kind"] == "error" and x["code"] == 0xE1 for x in rt)
        print("[%s] ESTOP 后 VEL → 0xE1（门控）" % ("PASS" if rej else "FAIL"))
        ok = ok and rej

        print("RESULT(M): %s" % ("PASS" if ok else "FAIL"))
        print("→ H 级请确认：电机此刻是否已断电自由滑停/可手动转动？")
    finally:
        b.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
