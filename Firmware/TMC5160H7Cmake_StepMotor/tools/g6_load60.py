#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""g6_load60.py — U1+U2 并发 0x04 速度模式 G6(1200rpm) 连续 60s（协议 v2）

判据（本轮任务）：ACK 收到才计时；60s 到发 0x07 停止并收 U1/U2 到位帧；
遥测 dev/es/gs/ds 由 RTT 侧另行解析（本脚本管 CAN 收发与停机保证）。
退出码: 0=跑满且停止帧收齐; 3=0x04 无 ACK（禁盲跑）; 4=停止反馈缺失; 2=异常（finally 已发停机帧）。
"""
import json
import sys
import time

import can

CMD_ID = 0x1AA55F42
FB_ID = 0x1AA55F43
CK = {"VEL": bytes([0x04, 0x03, 0x06, 0, 0, 0, 0, 0x0D]),
      "STOP": bytes([0x07, 0x03, 0, 0, 0, 0, 0, 0x0A])}
RUN_S = 60.0
ACK_TO = 5.0
STOP_TO = 10.0


def fb_parse(d):
    if len(d) != 8 or (sum(d[:7]) & 0xFF) != d[7]:
        return None
    if all(x == d[0] for x in d[:7]) and d[0] in (0xE1, 0xE2, 0xE3, 0xE4):
        return {"kind": "error", "code": d[0]}
    if d[1:7] == bytes(6):
        return {"kind": "ack", "cmd": d[0]}
    return {"kind": "arrival", "cmd": d[0], "motor": d[1], "param": d[2],
            "arrived": d[3], "otw": d[4], "drverr": d[5]}


def main():
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    result = {"start": time.strftime("%F %T"), "vel_ack": False,
              "run_s": 0.0, "stop_ack": False, "arrivals": {}, "error_fb": None}
    vel_sent = False
    try:
        while bus.recv(timeout=0.2) is not None:
            pass
        bus.send(can.Message(arbitration_id=CMD_ID, is_extended_id=True,
                             data=CK["VEL"]))
        vel_sent = True
        t0 = time.monotonic()
        while time.monotonic() - t0 < ACK_TO:
            msg = bus.recv(timeout=max(0.0, ACK_TO - (time.monotonic() - t0)))
            if msg is None or msg.arbitration_id != FB_ID:
                continue
            fb = fb_parse(bytes(msg.data))
            if fb and fb["kind"] == "error":
                result["error_fb"] = fb["code"]
                break
            if fb and fb["kind"] == "ack" and fb["cmd"] == 0x04:
                result["vel_ack"] = True
                break
        if not result["vel_ack"]:
            return 3
        t_run = time.monotonic()
        while time.monotonic() - t_run < RUN_S:
            time.sleep(0.2)
        result["run_s"] = time.monotonic() - t_run
        bus.send(can.Message(arbitration_id=CMD_ID, is_extended_id=True,
                             data=CK["STOP"]))
        vel_sent = False
        t1 = time.monotonic()
        while time.monotonic() - t1 < STOP_TO:
            msg = bus.recv(timeout=max(0.0, STOP_TO - (time.monotonic() - t1)))
            if msg is None or msg.arbitration_id != FB_ID:
                continue
            fb = fb_parse(bytes(msg.data))
            if fb is None:
                continue
            if fb["kind"] == "ack" and fb["cmd"] == 0x07:
                result["stop_ack"] = True
            elif fb["kind"] == "arrival" and fb["cmd"] == 0x07:
                result["arrivals"][fb["motor"]] = {
                    k: fb[k] for k in ("arrived", "otw", "drverr")}
            if result["stop_ack"] and len(result["arrivals"]) == 2:
                break
        rc = 0 if (result["stop_ack"] and len(result["arrivals"]) == 2) else 4
        result["end"] = time.strftime("%F %T")
        result["rc"] = rc
        return rc
    except Exception as e:
        result["exception"] = repr(e)
        return 2
    finally:
        if vel_sent:
            try:
                bus.send(can.Message(arbitration_id=CMD_ID,
                                     is_extended_id=True, data=CK["STOP"]))
            except Exception:
                pass
        bus.shutdown()
        out = ".cl/capture/g6_load60.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
