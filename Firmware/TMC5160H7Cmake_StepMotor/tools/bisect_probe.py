#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bisect_probe.py — G6 重载二分单点探针（协议 v2，改造自 g6_load60.py）

单进程占 PCAN：0x08 建基线 -> 脏门 -> 0x04 双机速度模式 -> 有界读 RTT 日志尾部
-> 熔断(失步/冻结)即发 0x07 -> 0x07 停止 -> 0x08 重建基线 -> stdout 单行 JSON。

判据（2026-09-29 任务，速度模式）：
  速度模式(0x04)下 CLOSEDLOOP s_cmd_target 恒 0（0x08 归零后不再更新），故
  CLOSEDLOOP_GetDeviation(=target-enc) 恒等于 -enc，随电机转动线性暴涨，
  **不能**作为失步判据。真实失步签名（见 09-28 rtt_g6_load60_window.log）：
  X_ENC 冻结而 XACTUAL 照爬 → 编码器滞后 lag=|XACTUAL-X_ENC| 发散。
  故本探针判据 = RTT [t=] 遥测 1Hz 采样的 lag：pass=窗口内 maxlag<=tol 且
  es/gs/ds 无失步/OT 置位；熔断=maxlag>fuse(640) 或 es!=0/gs!=0/保护位/OT
  或 enc 冻结(act 推进而 enc 不动)。

硬超时 duration+15s 必发 0x07(finally)。
退出码: 0=pass 1=stall(熔断或超限) 2=异常 3=0x04无ACK(禁盲跑) 5=dirty(基线脏/0x08失败)
"""
import argparse
import json
import os
import re
import sys
import time

import can

CMD_ID = 0x1AA55F42
FB_ID = 0x1AA55F43

# DRV_STATUS 保护位掩码：bit26 OTPW / bit25 OT / bit27 S2GA / bit28 S2GB /
#   bit12 S2VSA / bit13 S2VSB（对齐 can_cmd.c CAN_CMD_Protect）
DS_FAULT_MASK = 0x1E003000
DS_OT = 0x02000000


def frame(cmd, motor, param=0, value=0):
    d = bytearray(8)
    d[0] = cmd
    d[1] = motor
    d[2] = param
    d[3] = (value >> 24) & 0xFF
    d[4] = (value >> 16) & 0xFF
    d[5] = (value >> 8) & 0xFF
    d[6] = value & 0xFF
    d[7] = sum(d[:7]) & 0xFF
    return bytes(d)


RE_TELE = re.compile(
    r"\[t=(\d+)\] U1 act=(-?\d+) enc=(-?\d+) \| U2 act=(-?\d+) enc=(-?\d+) "
    r"ds=([0-9A-Fa-f]+) gs=([0-9A-Fa-f]+) es=([0-9A-Fa-f]+)")


def fb_parse(d):
    if len(d) != 8 or (sum(d[:7]) & 0xFF) != d[7]:
        return None
    if all(x == d[0] for x in d[:7]) and d[0] in (0xE1, 0xE2, 0xE3, 0xE4):
        return {"kind": "error", "code": d[0]}
    if d[1:7] == bytes(6):
        return {"kind": "ack", "cmd": d[0]}
    return {"kind": "arrival", "cmd": d[0], "motor": d[1], "param": d[2],
            "arrived": d[3], "otw": d[4], "drverr": d[5]}


class Bus:
    def __init__(self):
        self.bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1",
                           bitrate=500000)

    def send(self, data):
        self.bus.send(can.Message(arbitration_id=CMD_ID,
                                  is_extended_id=True, data=data))

    def drain(self):
        while self.bus.recv(timeout=0.05) is not None:
            pass

    def wait(self, cmd, kinds, timeout):
        """返回 (fb, err)；kinds 内类型命中即返回，error 提前返回"""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            msg = self.bus.recv(timeout=0.2)
            if msg is None or msg.arbitration_id != FB_ID:
                continue
            fb = fb_parse(bytes(msg.data))
            if fb is None:
                continue
            if fb["kind"] == "error":
                return fb, fb["code"]
            if fb["kind"] in kinds:
                return fb, None
        return None, None


class RttTailer:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.tele = []      # [t=] 遥测样本(dict)
        self.maxlag = 0     # 窗口内最大 |act-enc|

    def seek_end(self):
        try:
            self.offset = os.path.getsize(self.path)
        except OSError:
            self.offset = 0

    def poll(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size <= self.offset:
            if size < self.offset:
                self.offset = 0
            return
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.offset)
            data = f.read(size - self.offset)
            self.offset = f.tell()
        for line in data.splitlines():
            m = RE_TELE.search(line)
            if not m:
                continue
            t = int(m.group(1))
            u1_act, u1_enc = int(m.group(2)), int(m.group(3))
            u2_act, u2_enc = int(m.group(4)), int(m.group(5))
            lag = max(abs(u1_act - u1_enc), abs(u2_act - u2_enc))
            self.tele.append({
                "t": t, "u1_act": u1_act, "u1_enc": u1_enc,
                "u2_act": u2_act, "u2_enc": u2_enc, "lag": lag,
                "ds": int(m.group(6), 16), "gs": int(m.group(7), 16),
                "es": int(m.group(8), 16)})
            self.maxlag = max(self.maxlag, lag)

    def fresh_lag(self, within_s):
        """近 within_s 秒(按 RTT t 差)的最大 lag；无数据返回 None"""
        if not self.tele:
            return None
        last_t = self.tele[-1]["t"]
        vals = [s["lag"] for s in self.tele if last_t - s["t"] <= within_s * 1000]
        return max(vals) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vmax-raw", type=int, required=True)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--rtt-log", required=True)
    ap.add_argument("--tol", type=int, default=512)
    ap.add_argument("--fuse-dev", type=int, default=640)
    ap.add_argument("--bkp0r", default="")
    args = ap.parse_args()

    exp_speed = args.vmax_raw * 15000000.0 / (1 << 24)   # µsteps/s @fCLK=15MHz
    v = {"verdict": "error", "raw": args.vmax_raw, "duration": args.duration,
         "maxdev": None, "es": 0, "gs": 0, "ds": 0, "ot": False,
         "stop_sent": False, "zero_sent": False, "t_stall_ms": None,
         "vel_ack": False, "stop_ack": False, "arrivals": {},
         "zero_err": None, "bkp0r": args.bkp0r,
         "start": time.strftime("%F %T"), "reason": ""}
    bus = None
    motion_started = False
    try:
        bus = Bus()
        rt = RttTailer(args.rtt_log)

        # --- 0x08 建基线 + 脏门 ---
        rt.seek_end()
        zero_ok = False
        for _ in range(3):
            bus.drain()
            bus.send(frame(0x08, 0x03, 0, 0))
            fb, err = bus.wait(0x08, ("ack",), 6.0)
            if fb and fb["kind"] == "ack":
                zero_ok = True
                break
            if err in (0xE2,):
                time.sleep(2.0)
                continue
            if err == 0xE1:
                v["zero_err"] = err
                v["verdict"] = "dirty"
                v["reason"] = "0x08被拒:未校准(0xE1)"
                return 5
            time.sleep(1.0)
        if not zero_ok:
            v["verdict"] = "dirty"
            v["reason"] = "0x08无ACK(通信失败)"
            return 5
        v["zero_sent"] = True
        deadline_clean = time.monotonic() + 6.0
        lag0 = None
        while time.monotonic() < deadline_clean:
            rt.poll()
            lag0 = rt.fresh_lag(2.0)
            if lag0 is not None and lag0 < 100:
                break
            time.sleep(0.2)
        if lag0 is None or lag0 >= 100:
            v["verdict"] = "dirty"
            v["reason"] = "0x08后基线仍脏 maxlag=%s" % lag0
            return 5

        # --- 0x04 速度模式（无ACK禁盲跑）---
        rt.tele.clear()
        rt.maxlag = 0
        bus.drain()
        bus.send(frame(0x04, 0x03, 0x06, 0))
        fb, err = bus.wait(0x04, ("ack",), 5.0)
        if not (fb and fb["kind"] == "ack"):
            v["vel_ack"] = False
            v["reason"] = "0x04无ACK err=%s" % err
            return 3
        v["vel_ack"] = True
        motion_started = True

        # --- 监测循环（1Hz [t=] 遥测；滞后/故障位/冻结判据）---
        t_run = time.monotonic()
        fused = None
        es_or = gs_or = 0
        ds_last = 0
        prev = None
        while time.monotonic() - t_run < args.duration:
            rt.poll()
            if rt.maxlag > args.fuse_dev:
                fused = fused or ("maxlag>%d" % args.fuse_dev)
            if rt.tele:
                tl = rt.tele[-1]
                if tl is not prev:
                    prev = tl
                    es_or |= tl["es"]
                    gs_or |= tl["gs"]
                    ds_last = tl["ds"]
                    if tl["es"] or tl["gs"] or (tl["ds"] & DS_FAULT_MASK):
                        fused = fused or ("ds/es/gs故障 es=%02X gs=%02X "
                                          "ds=%08X" % (tl["es"], tl["gs"],
                                                       tl["ds"]))
                    if len(rt.tele) >= 2:
                        a, b = rt.tele[-2], rt.tele[-1]
                        for pfx in ("u1", "u2"):
                            da = abs(b[pfx + "_act"] - a[pfx + "_act"])
                            de = abs(b[pfx + "_enc"] - a[pfx + "_enc"])
                            dt_ms = max(1, b["t"] - a["t"])
                            if da > exp_speed * dt_ms / 1000.0 * 0.5 and de < 512:
                                fused = fused or ("%s enc冻结(act+%d/enc+%d)"
                                                  % (pfx, da, de))
            if fused:
                break
            time.sleep(0.1)
        v["es"] = es_or
        v["gs"] = gs_or
        v["ds"] = ds_last
        v["ot"] = bool(ds_last & DS_OT)
        v["maxdev"] = rt.maxlag
        run_s = time.monotonic() - t_run
        if fused:
            v["t_stall_ms"] = int(run_s * 1000)
            v["reason"] = fused

        # --- 0x07 停止 ---
        bus.drain()
        bus.send(frame(0x07, 0x03, 0, 0))
        v["stop_sent"] = True
        fb, err = bus.wait(0x07, ("ack",), 6.0)
        v["stop_ack"] = bool(fb and fb["kind"] == "ack")
        t1 = time.monotonic()
        while time.monotonic() - t1 < 10.0 and len(v["arrivals"]) < 2:
            fb, err = bus.wait(0x07, ("arrival",), 2.0)
            if fb and fb["kind"] == "arrival" and fb["cmd"] == 0x07:
                v["arrivals"][fb["motor"]] = {k: fb[k] for k in
                                              ("arrived", "otw", "drverr")}
            elif fb is None and err is None:
                break

        # --- verdict ---
        if (fused or rt.maxlag > args.tol or es_or or gs_or
                or (ds_last & DS_FAULT_MASK) or v["ot"]):
            v["verdict"] = "stall"
        else:
            v["verdict"] = "pass"

        # --- 0x08 重建基线（下一点复用）---
        rt.seek_end()
        bus.drain()
        bus.send(frame(0x08, 0x03, 0, 0))
        fb, err = bus.wait(0x08, ("ack",), 6.0)
        v["zero_sent"] = bool(fb and fb["kind"] == "ack")
        v["end"] = time.strftime("%F %T")
        v["run_s"] = round(run_s, 2)
        return 0 if v["verdict"] == "pass" else 1
    except Exception as e:
        v["exception"] = repr(e)
        return 2
    finally:
        if motion_started and not v["stop_sent"] and bus is not None:
            try:
                bus.send(frame(0x07, 0x03, 0, 0))
                v["stop_sent"] = True
            except Exception:
                pass
        if bus is not None:
            try:
                bus.bus.shutdown()
            except Exception:
                pass
        print(json.dumps(v, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())
