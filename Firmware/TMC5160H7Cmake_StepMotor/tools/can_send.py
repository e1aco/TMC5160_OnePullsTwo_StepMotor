#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""can_send.py — cl 闭环用 CAN 命令发送器（板端协议 require.md: 0x1AA55F42）

帧: value(4 LE) + cmd + motor + param + checksum(byte0..6 累加 & 0xFF)
用法:
  python can_send.py abs 51200 2 3        # 绝对定位 U2 参数组3
  python can_send.py rel_cw 51200 2 3     # 相对正转
  python can_send.py vel 51200 2 3        # 速度模式
  python can_send.py stop 2               # 停止 U2
  python can_send.py cl 1 2               # 闭环使能/禁用 (0/1)
  python can_send.py zero 6               # 在线清零（0x0B，motor=6 全部）
  python can_send.py raw 00 C8 00 00 01 02 03 CE
"""
import sys, time
import can

CAN_ID = 0x1AA55F42

CMDS = {"abs": 0x01, "rel_cw": 0x02, "rel_ccw": 0x03, "vel": 0x04,
        "stop": 0x05, "pid": 0x06, "cl": 0x07, "zero": 0x0B}


def build(value, cmd, motor, param):
    data = [(value >> (8 * i)) & 0xFF for i in range(4)]
    data += [cmd, motor, param]
    data.append(sum(data[:7]) & 0xFF)
    return bytearray(data)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args[0] == "raw":
        data = bytearray(int(x, 16) for x in args[1:9])
        print("raw:", " ".join("%02X" % b for b in data))
    else:
        kind = args[0]
        if kind == "stop":
            value, motor, param = 0, int(args[1]), 0
        elif kind == "zero":
            value, motor, param = 0, int(args[1]), 0
        elif kind == "cl":
            value, motor, param = int(args[1]), int(args[2]), 0
        else:
            value, motor, param = int(args[1]), int(args[2]), int(args[3])
        cmd = CMDS[kind]
        data = build(value, cmd, motor, param)
        print("%s: value=%d cmd=%02X motor=%02X param=%02X -> %s"
              % (kind, value, cmd, motor, param,
                 " ".join("%02X" % b for b in data)))
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
    msg = can.Message(arbitration_id=CAN_ID, is_extended_id=True, data=data)
    bus.send(msg)
    print("sent at", time.strftime("%H:%M:%S"))
    bus.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
