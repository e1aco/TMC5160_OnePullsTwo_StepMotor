#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""soak_3h.py - 长时单向 soak（G1~G6 每档 N 小时，相对 +travel 循环）— 协议 v2.1

依据 require.md 附录 A v2.1 + .cl/memory/config.md：
  X_ENC(编码器位置, R 0x39) 与 XACTUAL 同量纲(实测ΔENC/ΔACT≈0.999)；32 位上限 2.147e9；
  cum 接近阈值时发 0x08 在线清零（先 0x07 STOP 到终态），清零后继续同向。
  判据沿用 aging v2.1（终态到位 bit0=1 且状态位 bit1..4=0 且 |dev|<=tol）；
  位置源 = 0x09 Can[2]=1（X_ENC，编码器滞后 maxdev 的核心量）。

协议 v2.1：命令 0x1AA55F42 Can[0]=cmd/Can[1]=motor/Can[2]=param/Can[3..6]=value 大端；
  反馈 0x1AA55F43 ACK(Can[1..6]=0)/到位(.../状态位)/位置帧 0x09/错误(全等 0xE1-E4)。

前置：开跑前 24V 断电重建 TMC 基线（+MCU 复位），使 cum 与硬件计数 1:1；
  Windows 睡眠须关闭，否则 USB/PCAN 中断致误判 FAIL。

用法：
  python tools/soak_3h.py --param=5 [--hours=3] [--travel=512000]
                           [--zero-thresh=1000000000] [--tol=1024]
"""
import sys
import time

import can

CMD_ID = 0x1AA55F42
FB_ID = 0x1AA55F43

CMD_REL_CW = 0x02
CMD_STOP = 0x07     # v2: 0x07=停止/急停（value=0 停止）
CMD_ZERO = 0x08     # v2: 0x08=在线清零
CMD_READ_POS = 0x09
POS_SRC_X_ENC = 1   # 0x09 Can[2]=1 → X_ENC（编码器计数）

MOTOR_U1 = 0x01
MOTOR_U2 = 0x02
MOTOR_ALL = 0x03   # 位掩码：bit(i)=电机 i+1（0x03=U1+U2）

ERRS = {0xE1, 0xE2, 0xE3, 0xE4}

ACK_TIMEOUT_S = 8.0
DEV_TOL_DEFAULT = 1024
ZERO_THRESH_DEFAULT = 1000000000  # < 2^31-512000，留整腿裕量
ZERO_TIMEOUT_S = 30.0
POS_TIMEOUT_S = 2.0

LEG_RETRY_MAX = 4
ZERO_RETRY_MAX = 3

VPHYS = {1: 4473, 2: 17893, 3: 44733, 4: 89466, 5: 512000, 6: 1024000}


def build_cmd(value, cmd, motor, param):
    u = int(value) & 0xFFFFFFFF
    data = [cmd, motor, param,
            (u >> 24) & 0xFF, (u >> 16) & 0xFF, (u >> 8) & 0xFF, u & 0xFF]
    data.append(sum(data) & 0xFF)
    return bytearray(data)


def parse_fb(msg):
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
        return {"kind": "position", "motor": d[1], "status": d[2],
                "pos": int.from_bytes(d[3:7], "big", signed=True)}
    return {"kind": "arrival", "cmd": d[0], "motor": d[1], "param": d[2],
            "arrived": d[3], "otw": d[4], "drverr": d[5], "status": d[6]}


class Rx:
    def __init__(self, bus, log):
        self.bus = bus
        self.log = log
        self.acks = []
        self.arrivals = {}
        self.positions = {}

    def ingest(self, fb):
        if fb["kind"] == "ack":
            self.acks.append(fb["cmd"])
        elif fb["kind"] == "arrival":
            self.arrivals[(fb["cmd"], fb["motor"])] = fb
        elif fb["kind"] == "position":
            self.positions[(CMD_READ_POS, fb["motor"])] = fb

    def pump(self, deadline, done):
        while time.monotonic() < deadline:
            m = self.bus.recv(timeout=max(0.0, deadline - time.monotonic()))
            if m is None:
                break
            fb = parse_fb(m)
            if fb is None:
                continue
            self.ingest(fb)
            if done():
                return True
        return done()

    def send(self, value, cmd, motor, param=0):
        self.bus.send(can.Message(arbitration_id=CMD_ID, is_extended_id=True,
                                  data=build_cmd(value, cmd, motor, param)))

    def wait_ack(self, cmd, deadline):
        mark = len(self.acks)
        self.pump(deadline, lambda: self.acks[mark:].count(cmd) >= 1)
        return self.acks[mark:].count(cmd) >= 1

    def wait_arrivals(self, cmd, motors, deadline):
        for m in motors:
            self.arrivals.pop((cmd, m), None)
        self.pump(deadline,
                  lambda: all((cmd, m) in self.arrivals for m in motors))
        return {m: self.arrivals.get((cmd, m)) for m in motors
                if (cmd, m) in self.arrivals}

    def read_pos_us(self, motors, src=POS_SRC_X_ENC, timeout=POS_TIMEOUT_S):
        """发 0x09 读位置 → {motor: 位置}。

        X_ENC 与 XACTUAL 实测同量纲（ΔENC/ΔACT≈0.999，2026-09-30 标定），
        故无需 ×ENC_CONST 换算，直接与 µsteps 目标比较。
        """
        mask = 0
        for m in motors:
            mask |= m
        for m in motors:
            self.positions.pop((CMD_READ_POS, m), None)
        self.send(0, CMD_READ_POS, mask, src)
        self.pump(time.monotonic() + timeout,
                  lambda: all((CMD_READ_POS, m) in self.positions
                              for m in motors))
        out = {}
        for m in motors:
            fb = self.positions.get((CMD_READ_POS, m))
            if fb is not None:
                out[m] = fb["pos"]
        return out

    def drain(self, timeout=0.3):
        while self.bus.recv(timeout=timeout) is not None:
            pass


def stop_snapshot(rx, log, tag, timeout):
    """双电机 0x07 STOP → ACK + 终态 → 0x09(X_ENC) 位置快照 {m: µsteps}。"""
    rx.drain()
    for m in (MOTOR_U1, MOTOR_U2):
        rx.send(0, CMD_STOP, m, 0)
    rx.wait_ack(CMD_STOP, time.monotonic() + ACK_TIMEOUT_S)
    afins = rx.wait_arrivals(CMD_STOP, (MOTOR_U1, MOTOR_U2),
                             time.monotonic() + timeout)
    snap = rx.read_pos_us((MOTOR_U1, MOTOR_U2))
    log("%s stop arrivals=%s pos=%s" % (tag, sorted(afins.keys()), snap))
    return snap


def do_zero(rx, log, tol):
    """在线清零：STOP 快照 → 0x08 ALL → ACK → 0x09 双电机 pos≈0。成功 True。"""
    snap = stop_snapshot(rx, log, "zero-pre", ZERO_TIMEOUT_S)
    if len(snap) != 2:
        log("ZERO pre-stop 快照缺失 FAIL")
        return False
    rx.drain()
    rx.send(0, CMD_ZERO, MOTOR_ALL, 0)
    if not rx.wait_ack(CMD_ZERO, time.monotonic() + ACK_TIMEOUT_S):
        log("ZERO ACK 缺失 FAIL")
        return False
    got = rx.read_pos_us((MOTOR_U1, MOTOR_U2), timeout=ZERO_TIMEOUT_S)
    if len(got) != 2:
        log("ZERO 清零读数缺失 FAIL motors=%s" % sorted(got.keys()))
        return False
    ok = True
    for m in (MOTOR_U1, MOTOR_U2):
        good = abs(got[m]) <= tol
        ok = ok and good
        log("ZERO U%d pos=%d %s" % (m, got[m], "OK" if good else "FAIL"))
    return ok


def rebaseline(rx, log, tag):
    """瞬时失败后 STOP 重对位：返回新 exp（0x09 X_ENC），失败 None。"""
    snap = stop_snapshot(rx, log, tag, ZERO_TIMEOUT_S)
    if len(snap) != 2:
        log("%s 重对位失败" % tag)
        return None
    return snap


def main():
    param = 0
    hours = 3.0
    travel = 512000
    thresh = ZERO_THRESH_DEFAULT
    tol = DEV_TOL_DEFAULT
    pause = 0.0
    for a in sys.argv[1:]:
        if a.startswith("--param="):
            param = int(a.split("=")[1])
        elif a.startswith("--hours="):
            hours = float(a.split("=")[1])
        elif a.startswith("--travel="):
            travel = int(a.split("=")[1])
        elif a.startswith("--zero-thresh="):
            thresh = int(a.split("=")[1])
        elif a.startswith("--tol="):
            tol = int(a.split("=")[1])
        elif a.startswith("--pause="):
            pause = float(a.split("=")[1])
    if param not in VPHYS:
        print("need --param=1..6")
        return 2
    if travel >= thresh:
        print("travel 须 < zero-thresh（否则死循环清零）")
        return 2
    vphys = VPHYS[param]
    leg_timeout = max(45.0, travel / vphys * 1.5 + 30.0)
    log_path = ".cl/capture/soak_g%d.log" % param
    logf = open(log_path, "w", encoding="utf-8")

    def log(s):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), s)
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    log("soak v2.1 start param=G%d hours=%.2f travel=%d thresh=%d tol=%d "
        "pause=%.1fs leg_timeout=%.0fs retry=%d（先确认已 24V 断电重建基线！"
        "Windows 睡眠已关！）"
        % (param, hours, travel, thresh, tol, pause, leg_timeout,
           LEG_RETRY_MAX))
    try:
        bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                      bitrate=500000)
    except Exception as e:
        log("PCAN 打开失败 FAIL: %s" % e)
        return 2
    while bus.recv(timeout=0.2) is not None:
        pass
    rx = Rx(bus, log)
    try:
        base = stop_snapshot(rx, log, "P0", leg_timeout)
        if len(base) != 2:
            log("P0 获取失败 FAIL")
            return 1
        exp = dict(base)
        log("P0 U1=%d U2=%d（X_ENC µsteps 当量）" % (exp[MOTOR_U1], exp[MOTOR_U2]))
        t_start = time.monotonic()
        t_end = t_start + hours * 3600.0
        cum = 0
        legs = 0
        zeros = 0
        retries = 0
        last_beat = t_start
        last_dev = 0
        while time.monotonic() < t_end:
            if cum + travel > thresh:
                log("cum=%d 达阈值 → 在线清零" % cum)
                zero_ok = False
                for zt in range(ZERO_RETRY_MAX):
                    if do_zero(rx, log, tol):
                        zero_ok = True
                        break
                    if zt + 1 < ZERO_RETRY_MAX:
                        log("ZERO 第%d次失败 → 重试" % (zt + 1))
                        retries += 1
                        exp = rebaseline(rx, log, "zero-重试")
                        if exp is None:
                            rx.send(0, CMD_STOP, MOTOR_ALL, 0)
                            log("RESULT: FAIL zero-rebaseline")
                            return 1
                if not zero_ok:
                    rx.send(0, CMD_STOP, MOTOR_ALL, 0)
                    log("RESULT: FAIL zero")
                    return 1
                zeros += 1
                cum = 0
                exp = {MOTOR_U1: 0, MOTOR_U2: 0}
                log("ZERO done zeros=%d" % zeros)
                continue
            nxt = {m: exp[m] + travel for m in exp}
            leg_ok = False
            for lt in range(LEG_RETRY_MAX):
                rx.drain()
                rx.send(travel, CMD_REL_CW, MOTOR_ALL, param)
                t0 = time.monotonic()
                if not rx.wait_ack(CMD_REL_CW, t0 + ACK_TIMEOUT_S):
                    log("leg%d 第%d次 ACK 缺失（链路瞬断）" % (legs + 1, lt + 1))
                else:
                    finals = rx.wait_arrivals(CMD_REL_CW,
                                              (MOTOR_U1, MOTOR_U2),
                                              t0 + leg_timeout)
                    if len(finals) != 2:
                        log("leg%d 第%d次 终态超时（链路瞬断）"
                            % (legs + 1, lt + 1))
                    else:
                        pos = rx.read_pos_us((MOTOR_U1, MOTOR_U2))
                        if len(pos) != 2:
                            log("leg%d 第%d次 0x09 读数缺失" % (legs + 1, lt + 1))
                        else:
                            hard = False
                            soft = False
                            for m in (MOTOR_U1, MOTOR_U2):
                                dev = pos[m] - nxt[m]
                                last_dev = max(last_dev, abs(dev))
                                if abs(dev) > tol:
                                    soft = True
                                    log("leg%d U%d dev=%+d (期望=%d) 超tol"
                                        % (legs + 1, m, dev, nxt[m]))
                                if finals[m]["status"] & 0x1E:
                                    hard = True
                                    log("leg%d U%d 硬故障位 st=%02X FAIL"
                                        % (legs + 1, m, finals[m]["status"]))
                            if hard:
                                rx.send(0, CMD_STOP, MOTOR_ALL, 0)
                                log("RESULT: FAIL leg=%d fault" % (legs + 1))
                                return 1
                            if not soft:
                                leg_ok = True
                if leg_ok:
                    break
                if lt + 1 < LEG_RETRY_MAX:
                    retries += 1
                    exp = rebaseline(rx, log, "leg-重对位")
                    if exp is None:
                        rx.send(0, CMD_STOP, MOTOR_ALL, 0)
                        log("RESULT: FAIL leg-rebaseline")
                        return 1
                    nxt = {m: exp[m] + travel for m in exp}
            if not leg_ok:
                rx.send(0, CMD_STOP, MOTOR_ALL, 0)
                log("RESULT: FAIL leg=%d retries耗尽" % (legs + 1))
                return 1
            exp = nxt
            cum += travel
            legs += 1
            if pause > 0.0:
                time.sleep(pause)
            now = time.monotonic()
            if now - last_beat >= 60.0:
                last_beat = now
                log("beat legs=%d elapsed=%.0fs/%.0fs cum=%d zeros=%d "
                    "retries=%d maxdev=%d"
                    % (legs, now - t_start, hours * 3600.0, cum, zeros,
                       retries, last_dev))
        log("RESULT: PASS param=G%d legs=%d zeros=%d retries=%d "
            "maxdev=%d t=%.0fs"
            % (param, legs, zeros, retries, last_dev,
               time.monotonic() - t_start))
        return 0
    except Exception as e:
        log("异常 FAIL: %s" % e)
        try:
            rx.send(0, CMD_STOP, MOTOR_ALL, 0)
        except Exception:
            pass
        return 2
    finally:
        try:
            bus.shutdown()
        except Exception:
            pass
        logf.close()


if __name__ == "__main__":
    sys.exit(main())
