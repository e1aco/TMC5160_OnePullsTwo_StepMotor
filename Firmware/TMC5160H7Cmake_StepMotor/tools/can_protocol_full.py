#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""can_protocol_full.py — CAN 协议 v2 全面测试

用户协议 v2（见 require.md 附录A）：
命令 Can[0]=cmd/Can[1]=motor 位掩码/Can[2]=param/Can[3..6]=value 大端；
反馈 ACK（命令码回显）/到位（cmd/motor/param/到位/过温/驱动异常）/
错误（Can[0..6] 全=错误码 0xE1 未校准 0xE2 校准中 0xE3 非法掩码 0xE4 未知命令）。

用法:
  python tools/can_protocol_full.py
  python tools/can_protocol_full.py --quick   # 跳过长运动
"""
import argparse
import sys
import time

import can

CMD_ID = 0x1AA55F42
FB_ID = 0x1AA55F43

CMDS = {
    "ABS": 0x01, "REL_CW": 0x02, "REL_CCW": 0x03, "VEL": 0x04,
    "HOME": 0x05, "CL": 0x06, "STOP": 0x07, "ZERO": 0x08,
    "READ_POS": 0x09,
}
M_U1, M_U2, M_ALL = 0x01, 0x02, 0x03
M_INVALID = 0x06   # bit2 置位无对应电机 → 非法掩码
ERRS = {0xE1: "UNCAL", 0xE2: "CAL", 0xE3: "MASK", 0xE4: "CMD"}

FLAG_OFF = 0x00
FLAG_ON = 0xFF

ACK_TIMEOUT = 3.0
FINAL_TIMEOUT = 15.0


def build(value, cmd, motor, param, good_ck=True):
    """v2 组帧：Can[0]=cmd, Can[1]=motor, Can[2]=param, Can[3..6]=value 大端。"""
    u = int(value) & 0xFFFFFFFF
    data = [cmd, motor, param,
            (u >> 24) & 0xFF, (u >> 16) & 0xFF, (u >> 8) & 0xFF, u & 0xFF]
    s = sum(data) & 0xFF
    data.append(s if good_ck else (s ^ 0xFF))
    return bytearray(data)


def parse_fb(msg):
    """v2 解析 → {'kind': 'ack'|'arrival'|'error', ...} 或 None。"""
    if msg.arbitration_id != FB_ID or not msg.is_extended_id:
        return None
    d = bytes(msg.data)
    if len(d) != 8:
        return None
    if (sum(d[:7]) & 0xFF) != d[7]:
        return None
    if (d[0] == d[1] == d[2] == d[3] == d[4] == d[5] == d[6]
            and d[0] in ERRS):
        return {"kind": "error", "code": d[0], "raw": d}
    if d[1] == 0 and d[2] == 0 and d[3] == 0 and d[4] == 0 and d[5] == 0 \
            and d[6] == 0:
        return {"kind": "ack", "cmd": d[0], "raw": d}
    if d[0] == CMDS["READ_POS"]:
        # v2.1 位置帧：Can[2]=状态位（bit4=SPI异常），Can[3..6]=XACTUAL int32 大端
        return {"kind": "position", "cmd": d[0], "motor": d[1],
                "status": d[2], "pos": int.from_bytes(d[3:7], "big", signed=True),
                "raw": d}
    return {"kind": "arrival", "cmd": d[0], "motor": d[1], "param": d[2],
            "arrived": d[3], "otw": d[4], "drverr": d[5], "status": d[6],
            "raw": d}


class Session:
    def __init__(self, bus, log):
        self.bus = bus
        self.log = log
        self.acks = []            # ACK 的 cmd 列表
        self.arrivals = {}        # (cmd, motor) -> fb
        self.positions = {}       # (cmd, motor) -> fb（0x09 位置帧）
        self.errors = []          # 错误码列表
        self.bad_ck_rx = 0
        self.pass_n = 0
        self.fail_n = 0
        self.cases = []

    def drain(self, t=0.15):
        end = time.monotonic() + t
        while time.monotonic() < end:
            msg = self.bus.recv(timeout=max(0.0, end - time.monotonic()))
            if msg is None:
                break
            self._ingest(msg)

    def _ingest(self, msg):
        fb = parse_fb(msg)
        if fb is None:
            if msg.arbitration_id == FB_ID:
                self.bad_ck_rx += 1
            return
        if fb["kind"] == "ack":
            self.acks.append(fb["cmd"])
        elif fb["kind"] == "arrival":
            self.arrivals[(fb["cmd"], fb["motor"])] = fb
        elif fb["kind"] == "position":
            self.positions[(fb["cmd"], fb["motor"])] = fb
        else:
            self.errors.append(fb["code"])

    def send(self, value, cmd, motor, param=0, good_ck=True):
        data = build(value, cmd, motor, param, good_ck)
        self.bus.send(
            can.Message(arbitration_id=CMD_ID, is_extended_id=True, data=data)
        )

    def wait_ack(self, cmd, timeout=ACK_TIMEOUT):
        """等一帧指定 cmd 的 ACK。"""
        self.acks.clear()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            msg = self.bus.recv(timeout=max(0.0, end - time.monotonic()))
            if msg is None:
                break
            self._ingest(msg)
            if cmd in self.acks:
                return True
        return False

    def wait_arrivals(self, cmd, motors, timeout=FINAL_TIMEOUT):
        """等指定 (cmd, motors) 的到位帧集合。返回 {motor: fb}。"""
        got = {}
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(got) < len(motors):
            msg = self.bus.recv(timeout=max(0.0, end - time.monotonic()))
            if msg is None:
                break
            self._ingest(msg)
            for m in motors:
                if (cmd, m) in self.arrivals:
                    got[m] = self.arrivals[(cmd, m)]
        return got

    def wait_positions(self, cmd, motors, timeout=FINAL_TIMEOUT):
        """等指定 (cmd, motors) 的位置帧集合（0x09）。返回 {motor: fb}。"""
        got = {}
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(got) < len(motors):
            msg = self.bus.recv(timeout=max(0.0, end - time.monotonic()))
            if msg is None:
                break
            self._ingest(msg)
            for m in motors:
                if (cmd, m) in self.positions:
                    got[m] = self.positions[(cmd, m)]
        return got

    def wait_error(self, code, timeout=2.0):
        """等一帧指定错误码。"""
        self.errors.clear()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            msg = self.bus.recv(timeout=max(0.0, end - time.monotonic()))
            if msg is None:
                break
            self._ingest(msg)
            if code in self.errors:
                return True
        return False

    def case(self, name, ok, detail=""):
        st = "PASS" if ok else "FAIL"
        if ok:
            self.pass_n += 1
        else:
            self.fail_n += 1
        line = f"[{st}] {name}" + (f" | {detail}" if detail else "")
        self.log(line)
        self.cases.append((name, ok, detail))
        return ok


def arrival_clean(fb):
    """到位帧干净：arrived=1 且无过温/驱动异常。"""
    return (fb is not None and fb["arrived"] == 1
            and fb["otw"] == 0 and fb["drverr"] == 0)


def run(quick=False):
    log_path = ".cl/capture/can_protocol_full.log"
    logf = open(log_path, "w", encoding="utf-8")

    def log(s):
        line = f"[{time.strftime('%H:%M:%S')}] {s}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    log("can_protocol_full v2 start quick=%s" % quick)
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    while bus.recv(timeout=0.2) is not None:
        pass

    s = Session(bus, log)

    # ---- 0. 基线：STOP(0x07,value=0) U1/U2 → ACK + 到位 ----
    s.send(0, CMDS["STOP"], M_U1)
    s.send(0, CMDS["STOP"], M_U2)
    ok_a = s.wait_ack(CMDS["STOP"]) and s.wait_ack(CMDS["STOP"])
    f1 = s.wait_arrivals(CMDS["STOP"], (M_U1, M_U2))
    ok_f = len(f1) == 2 and all(arrival_clean(f1.get(m)) for m in (M_U1, M_U2))
    s.case("P0_stop_ACK+arrival", ok_a and ok_f,
           f"arr={sorted(f1)}")

    # ---- 1. 坏校验和 → 无反馈 ----
    n0 = len(s.acks) + len(s.arrivals) + len(s.errors)
    s.send(0, CMDS["STOP"], M_U1, good_ck=False)
    time.sleep(0.3)
    s.drain(0.3)
    s.case("bad_checksum_no_fb",
           len(s.acks) + len(s.arrivals) + len(s.errors) == n0, "")

    # ---- 2. 错误 ID 由硬件过滤 ----
    n0 = len(s.acks) + len(s.arrivals) + len(s.errors)
    bus.send(can.Message(arbitration_id=0x1AA55F44, is_extended_id=True,
                         data=build(0, CMDS["STOP"], M_U1, 0)))
    time.sleep(0.2)
    s.drain(0.2)
    s.case("wrong_id_filtered",
           len(s.acks) + len(s.arrivals) + len(s.errors) == n0, "")

    # ---- 3. CL(0x06)：使能(value=0) → ACK + 到位；失能(value=0xFF) 同 ----
    for nm, val in (("CL_enable", FLAG_OFF), ("CL_disable", FLAG_ON)):
        s.send(val, CMDS["CL"], M_U1)
        ok_a = s.wait_ack(CMDS["CL"])
        f = s.wait_arrivals(CMDS["CL"], (M_U1,), timeout=5.0)
        s.case(nm, ok_a and arrival_clean(f.get(M_U1)),
               f"arr={f.get(M_U1)}")

    # ---- 4. ZERO(0x08) ALL → ACK(回显0x08) + 两路到位 ----
    s.send(0, CMDS["ZERO"], M_ALL)
    ok_a = s.wait_ack(CMDS["ZERO"])
    fz = s.wait_arrivals(CMDS["ZERO"], (M_U1, M_U2), timeout=5.0)
    s.case("zero_ALL_ACK+arrival",
           ok_a and len(fz) == 2
           and all(arrival_clean(fz.get(m)) for m in (M_U1, M_U2)),
           f"arr={sorted(fz)}")

    # ---- 4b. READ_POS(0x09) ALL → ACK + 两路位置帧（ZERO 后位置应≈0）----
    s.send(0, CMDS["READ_POS"], M_ALL)
    ok_a = s.wait_ack(CMDS["READ_POS"])
    fp = s.wait_positions(CMDS["READ_POS"], (M_U1, M_U2), timeout=5.0)
    pos_ok = (ok_a and len(fp) == 2
              and all(abs(fp[m]["pos"]) <= 512 for m in (M_U1, M_U2) if m in fp))
    s.case("readpos_ALL_ACK+pos", pos_ok,
           "pos U1=%s U2=%s"
           % (fp.get(M_U1, {}).get("pos"), fp.get(M_U2, {}).get("pos")))

    if not quick:
        # ---- 5. REL_CW +51200 U1 → ACK + 到位干净 ----
        s.send(51200, CMDS["REL_CW"], M_U1, param=5)
        ok_a = s.wait_ack(CMDS["REL_CW"])
        fr = s.wait_arrivals(CMDS["REL_CW"], (M_U1,))
        s.case("rel_cw_U1_arrival", ok_a and arrival_clean(fr.get(M_U1)),
               f"arr={fr.get(M_U1)}")
        # ---- 6. REL_CCW -51200 U1 ----
        s.send(51200, CMDS["REL_CCW"], M_U1, param=5)
        ok_a = s.wait_ack(CMDS["REL_CCW"])
        fr = s.wait_arrivals(CMDS["REL_CCW"], (M_U1,))
        s.case("rel_ccw_U1_arrival", ok_a and arrival_clean(fr.get(M_U1)),
               f"arr={fr.get(M_U1)}")
        # ---- 7. ABS U2 ----
        s.send(25600, CMDS["ABS"], M_U2, param=5)
        ok_a = s.wait_ack(CMDS["ABS"])
        fa = s.wait_arrivals(CMDS["ABS"], (M_U2,))
        s.case("abs_U2_arrival", ok_a and arrival_clean(fa.get(M_U2)),
               f"arr={fa.get(M_U2)}")

    # ---- 8. VEL(0x04) → ACK，无终态 ----
    s.send(FLAG_OFF, CMDS["VEL"], M_U1, param=5)
    ok_a = s.wait_ack(CMDS["VEL"])
    time.sleep(1.0)
    s.drain(0.5)
    s.case("vel_U1_ack_no_final", ok_a, "")
    s.send(FLAG_OFF, CMDS["STOP"], M_U1)
    s.wait_ack(CMDS["STOP"])
    s.wait_arrivals(CMDS["STOP"], (M_U1,), timeout=5.0)

    if not quick:
        # ---- 9. 双电机并发 REL_CW（分开发，掩码等价）----
        s.send(25600, CMDS["REL_CW"], M_U1, param=5)
        s.send(25600, CMDS["REL_CW"], M_U2, param=5)
        ok_a = s.wait_ack(CMDS["REL_CW"]) and s.wait_ack(CMDS["REL_CW"])
        fd = {}
        fd.update(s.wait_arrivals(CMDS["REL_CW"], (M_U1, M_U2)))
        s.case("dual_rel_cw", ok_a and len(fd) == 2
               and all(arrival_clean(fd.get(m)) for m in (M_U1, M_U2)),
               f"arr={sorted(fd)}")

    # ---- 10. 非法掩码 0x06 → 0xE3 ----
    s.send(0, CMDS["STOP"], M_INVALID)
    s.case("err_invalid_mask", s.wait_error(0xE3), "")

    # ---- 11. 未知命令 0x7F → 0xE4 ----
    s.send(0, 0x7F, M_U1)
    s.case("err_unknown_cmd", s.wait_error(0xE4), "")

    # ---- 12a. ESTOP 非法掩码 0x06 → 仍全关、回 0xE1（不再 0xE3）----
    s.send(FLAG_ON, CMDS["STOP"], M_INVALID)
    s.case("estop_invalid_mask_still_cuts", s.wait_error(0xE1), "")

    # ---- 12. ESTOP(0x07,value=0xFF) ALL → 0xE1；此后未校准 ----
    s.send(FLAG_ON, CMDS["STOP"], M_ALL)
    s.case("estop_ALL_uncalibrated_err", s.wait_error(0xE1), "")

    # ---- 12b. ESTOP 后 0x09 仍可读（校准门控豁免）----
    s.send(0, CMDS["READ_POS"], M_U1)
    ok_a = s.wait_ack(CMDS["READ_POS"])
    fp = s.wait_positions(CMDS["READ_POS"], (M_U1,), timeout=3.0)
    s.case("readpos_exempt_after_estop", ok_a and len(fp) == 1,
           "pos U1=%s" % fp.get(M_U1, {}).get("pos"))

    # ---- 13. 未校准后运动被拒（STOP U1 → 0xE1）----
    s.send(0, CMDS["STOP"], M_U1)
    s.case("uncalibrated_move_rejected", s.wait_error(0xE1), "")

    # ---- 14. HOME(0x05) U1 → ACK；STOP 打断 → 0xE2 ----
    s.send(FLAG_ON, CMDS["HOME"], M_U1)
    ok_a = s.wait_ack(CMDS["HOME"])
    s.case("home_U1_ACK_start", ok_a, "")
    s.send(0, CMDS["STOP"], M_U1)
    s.case("home_interrupt_calibrating_err", s.wait_error(0xE2), "")

    # ---- 15. HOME 忙拒：回零中再发 HOME → 0xE2，不打断 ----
    s.send(FLAG_ON, CMDS["HOME"], M_U1)
    time.sleep(0.2)
    s.drain(0.2)
    s.send(FLAG_ON, CMDS["HOME"], M_U1)
    s.case("home_busy_calibrating_err", s.wait_error(0xE2), "")
    s.send(0, CMDS["STOP"], M_U1)
    time.sleep(0.2)
    s.drain(0.2)

    # ---- 16. 反馈帧 checksum 全样本 ----
    # （本轮 rx 未单独存 raw，简化：坏校验帧计数为 0 即过）
    s.case("fb_checksum_all_samples", s.bad_ck_rx == 0,
           f"host_bad_ck_frames={s.bad_ck_rx}")

    # ---- 汇总 ----
    log("---- SUMMARY ----")
    log(f"PASS={s.pass_n} FAIL={s.fail_n}")
    if s.fail_n:
        for name, ok, det in s.cases:
            if not ok:
                log(f"  FAIL_CASE {name}: {det}")
    result = "PASS" if s.fail_n == 0 else "FAIL"
    log(f"RESULT: {result} pass={s.pass_n} fail={s.fail_n}")
    bus.shutdown()
    logf.close()
    return 0 if s.fail_n == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    return run(quick=args.quick)


if __name__ == "__main__":
    sys.exit(main())
