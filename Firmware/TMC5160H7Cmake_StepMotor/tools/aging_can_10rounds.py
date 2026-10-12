#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aging_can_10rounds.py - CAN 驱动老化 10 轮（G5 600rpm 生产档，相对 ±10 圈来回）

依据 require.md 附录 A v2.1 + .cl/memory/config.md：
  profile5_vmax=572303(10rps=600rpm) / motor_counts_per_rev=51200 / encoder_tol=512
  → 行程 AGING_TRAVEL_STEPS=512000 µsteps / DEV_TOL=1024(=2*ENC_TOL)
协议 v2.1（require.md 附录 A）：
  命令 0x1AA55F42: Can[0]=cmd/Can[1]=motor 掩码/Can[2]=param/Can[3..6]=value 大端
  反馈 0x1AA55F43: ACK(Can[1..6]=0) / 到位(cmd/motor/param/到位/过温/驱动错/状态位)
                   / 位置帧 0x09(cmd/motor/状态位/XACTUAL int32 大端) / 错误(全等 0xE1-E4)
  v2 到位帧不再带位置值 → 每 leg 终态后用 0x09 读位置做偏差判据
判据（每 leg 每电机）：
  终态到位 bit0=1 且状态位 bit1..4=0（失步/过温/驱动错/SPI异常）
  0x09 位置 |pos - 期望pos| <= DEV_TOL(=1024)，报告实测偏差
  单 leg 30s 无终态 → FAIL，发 0x07 停止(锁轴)后退出
流程：stop 取 P0 → 10×[rel_cw +512000 → 终态 → 0x09 → 停3s →
                       rel_ccw -512000 → 终态 → 0x09 → 停3s]
用法：python tools/aging_can_10rounds.py [--rounds 10] [--param 5] [--travel 512000]
"""
import sys
import time

import can

CMD_ID = 0x1AA55F42
FB_ID = 0x1AA55F43

CMD_REL_CW = 0x02
CMD_REL_CCW = 0x03
CMD_STOP = 0x07     # v2: 0x07=停止/急停（value=0 停止）
CMD_READ_POS = 0x09

MOTOR_U1 = 0x01
MOTOR_U2 = 0x02
MOTOR_ALL = 0x03   # 位掩码：bit(i)=电机 i+1（0x03=U1+U2）

ERRS = {0xE1, 0xE2, 0xE3, 0xE4}

LEG_TIMEOUT_S = 30.0
ACK_TIMEOUT_S = 5.0
BETWEEN_LEGS_S = 3.0
TRAVEL_DEFAULT = 512000
PARAM_DEFAULT = 5
DEV_TOL = 1024          # = 2 * encoder_tol(512)，报告实测偏差
STATUS_FAULT_MASK = 0x1E  # 到位帧状态位 bit1..4：失步/过温/驱动错/SPI异常
POS_SPIFAIL = 0x10      # 位置帧状态位 bit4：SPI 异常


def build_cmd(value, cmd, motor, param):
    """v2 组帧：Can[0]=cmd, Can[1]=motor, Can[2]=param, Can[3..6]=value 大端。"""
    u = int(value) & 0xFFFFFFFF
    data = [cmd, motor, param,
            (u >> 24) & 0xFF, (u >> 16) & 0xFF, (u >> 8) & 0xFF, u & 0xFF]
    data.append(sum(data) & 0xFF)
    return bytearray(data)


def parse_fb(msg):
    """解析反馈帧 → dict（含 kind）或 None（长度/校验/非本帧时）。

    判定顺序：错误 → ACK → 位置(0x09) → 到位；ACK 先判以免 0x09 ACK 被当位置帧。
    """
    if msg.arbitration_id != FB_ID or not msg.is_extended_id:
        return None
    d = bytes(msg.data)
    if len(d) != 8 or (sum(d[:7]) & 0xFF) != d[7]:
        return None
    if d[0] == d[1] == d[2] == d[3] == d[4] == d[5] == d[6] and d[0] in ERRS:
        return {"kind": "error", "code": d[0]}
    if d[1] == 0 and d[2] == 0 and d[3] == 0 and d[4] == 0 and d[5] == 0 \
            and d[6] == 0:
        return {"kind": "ack", "cmd": d[0]}
    if d[0] == CMD_READ_POS:
        return {"kind": "position", "cmd": d[0], "motor": d[1], "status": d[2],
                "pos": int.from_bytes(d[3:7], "big", signed=True)}
    return {"kind": "arrival", "cmd": d[0], "motor": d[1], "param": d[2],
            "arrived": d[3], "otw": d[4], "drverr": d[5], "status": d[6]}


class Rx:
    """全量 ingest 收集器：收帧即入库，等待=查库（避免 ACK 窗丢错位帧）。"""

    def __init__(self, bus):
        self.bus = bus
        self.acks = []            # cmd 列表
        self.arrivals = {}        # (cmd, motor) -> fb
        self.positions = {}       # (cmd, motor) -> fb
        self.dbg = None

    def ingest(self, fb):
        if fb["kind"] == "ack":
            self.acks.append(fb["cmd"])
        elif fb["kind"] == "arrival":
            self.arrivals[(fb["cmd"], fb["motor"])] = fb
        elif fb["kind"] == "position":
            self.positions[(fb["cmd"], fb["motor"])] = fb

    def pump(self, deadline, done):
        while time.monotonic() < deadline:
            m = self.bus.recv(timeout=max(0.0, deadline - time.monotonic()))
            if m is None:
                break
            fb = parse_fb(m)
            if fb is None:
                continue
            if self.dbg is not None:
                self.dbg("rx kind=%s cmd=%02X motor=%s"
                         % (fb["kind"], fb.get("cmd", 0), fb.get("motor", 0)))
            self.ingest(fb)
            if done():
                return True
        return done()

    def send(self, value, cmd, motor, param=0):
        self.bus.send(can.Message(arbitration_id=CMD_ID, is_extended_id=True,
                                  data=build_cmd(value, cmd, motor, param)))

    def wait_acks(self, cmd, n, deadline):
        mark = len(self.acks)
        return self.pump(deadline,
                         lambda: self.acks[mark:].count(cmd) >= n)

    def wait_arrivals(self, cmd, motors, deadline):
        for m in motors:
            self.arrivals.pop((cmd, m), None)
        self.pump(deadline,
                  lambda: all((cmd, m) in self.arrivals for m in motors))
        return {m: self.arrivals.get((cmd, m)) for m in motors
                if (cmd, m) in self.arrivals}

    def read_positions(self, motors, timeout=2.0):
        mask = 0
        for m in motors:
            mask |= m
        for m in motors:
            self.positions.pop((CMD_READ_POS, m), None)
        self.send(0, CMD_READ_POS, mask, 0)
        self.pump(time.monotonic() + timeout,
                  lambda: all((CMD_READ_POS, m) in self.positions
                              for m in motors))
        return {m: self.positions.get((CMD_READ_POS, m)) for m in motors
                if (CMD_READ_POS, m) in self.positions}


def one_leg(rx, log, leg_no, direction, travel, param, expect):
    """单 leg：双电机同向相对运动。expect={motor: 期望终态pos}。返回 (ok, info)。"""
    motors = (MOTOR_U1, MOTOR_U2)
    cmd = CMD_REL_CW if direction > 0 else CMD_REL_CCW
    for m in motors:
        rx.send(travel, cmd, m, param)
    t0 = time.monotonic()
    if not rx.wait_acks(cmd, len(motors), t0 + ACK_TIMEOUT_S):
        return False, "ACK缺失"
    finals = rx.wait_arrivals(cmd, motors, t0 + LEG_TIMEOUT_S)
    if len(finals) != len(motors):
        return False, "终态超时 motors=%s" % sorted(finals.keys())
    pos = rx.read_positions(motors)
    if len(pos) != len(motors):
        return False, "0x09位置缺失 motors=%s" % sorted(pos.keys())
    dt = time.monotonic() - t0
    parts = []
    ok = True
    for m in motors:
        fb = finals[m]
        p = pos[m]
        dev = p["pos"] - expect[m]
        fault = (fb["status"] & STATUS_FAULT_MASK) or (p["status"] & POS_SPIFAIL)
        leg_ok = (abs(dev) <= DEV_TOL) and (fault == 0)
        ok = ok and leg_ok
        parts.append("U%d pos=%d 期望=%d dev=%+d st=%02X pst=%02X %s"
                     % (m, p["pos"], expect[m], dev, fb["status"], p["status"],
                        "OK" if leg_ok else "FAIL"))
    info = "leg%d %s t=%.1fs | %s" % (leg_no, "+" if direction > 0 else "-",
                                      dt, " | ".join(parts))
    log(info)
    return ok, info


def main():
    rounds = 10
    param = PARAM_DEFAULT
    travel = TRAVEL_DEFAULT
    for a in sys.argv[1:]:
        if a.startswith("--rounds="):
            rounds = int(a.split("=")[1])
        elif a.startswith("--param="):
            param = int(a.split("=")[1])
        elif a.startswith("--travel="):
            travel = int(a.split("=")[1])
    log_path = ".cl/capture/aging_can.log"
    logf = open(log_path, "w", encoding="utf-8")

    def log(s):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), s)
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    log("aging_can v2.1 start rounds=%d param=G%d travel=%d tol=%d"
        % (rounds, param, travel, DEV_TOL))
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    while bus.recv(timeout=0.2) is not None:   # 排空总线残留帧
        pass
    rx = Rx(bus)
    rx.dbg = log

    # P0：逐电机 stop(0x07,value=0) 锁轴 → ACK + 终态；再 0x09 取当前位置
    for m in (MOTOR_U1, MOTOR_U2):
        rx.send(0, CMD_STOP, m, 0)
    t0 = time.monotonic()
    rx.wait_acks(CMD_STOP, 2, t0 + ACK_TIMEOUT_S)
    afins = rx.wait_arrivals(CMD_STOP, (MOTOR_U1, MOTOR_U2),
                             t0 + LEG_TIMEOUT_S)
    if len(afins) != 2:
        log("P0 终态获取失败 motors=%s FAIL" % sorted(afins.keys()))
        bus.shutdown()
        return 1
    pos0 = rx.read_positions((MOTOR_U1, MOTOR_U2))
    if len(pos0) != 2:
        log("P0 位置获取失败 motors=%s FAIL" % sorted(pos0.keys()))
        bus.shutdown()
        return 1
    base = {m: pos0[m]["pos"] for m in (MOTOR_U1, MOTOR_U2)}
    log("P0 U1=%d U2=%d" % (base[MOTOR_U1], base[MOTOR_U2]))

    fails = 0
    cur = dict(base)
    leg_no = 0
    for r in range(1, rounds + 1):
        for direction in (+1, -1):
            leg_no += 1
            nxt = {m: cur[m] + direction * travel for m in cur}
            ok, _ = one_leg(rx, log, leg_no, direction, travel, param, nxt)
            if not ok:
                fails += 1
                log("cycle=%d FAIL → stop 锁轴退出" % r)
                rx.send(0, CMD_STOP, MOTOR_ALL, 0)
                bus.shutdown()
                log("RESULT: FAIL round=%d fails=%d" % (r, fails))
                return 1
            cur = nxt
        time.sleep(BETWEEN_LEGS_S)
        log("cycle=%d t累计=%.0fs PASS" % (r, time.monotonic() - t0))
        time.sleep(BETWEEN_LEGS_S)
    bus.shutdown()
    log("RESULT: PASS rounds=%d fails=0" % rounds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
