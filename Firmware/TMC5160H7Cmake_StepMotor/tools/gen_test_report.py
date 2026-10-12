#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_test_report.py — 按桌面模板「测试计划表及总结报告.xlsx」填充并另存。"""
import os
import openpyxl

SRC = r"C:\Users\admin\Desktop\测试计划表及总结报告.xlsx"
DST = r"C:\Users\admin\Desktop\测试计划表及总结报告_驱动板整体测试_20260930.xlsx"

DESC = (
    "STM32H750VBT6 + 双 TMC5160 + 57CME13(2相4A/1.3N·m) + 1000PPR光编(×4=4000cpr，"
    "ENC_CONST=12.8)；24V+传送带。固件内 pre-RTOS 老化：双电机开环 ±1圈"
    "(51200µsteps/腿)往复，逐腿读 XACTUAL/X_ENC 偏差与 GSTAT/DRVSTATUS/ENC_STATUS "
    "故障位；15s 启动延时；循环内喂狗；结果存后备SRAM(BKPSRAM)环形。"
)
PURPOSE = (
    "测定各档速度(G1~G6，约 5~610rpm)长时间往复老化的失步/故障/温升，确定最高不失步"
    "稳定转速；并验证 SPI 通信、编码器一致性、急停断功率、CAN 协议等整体通路。"
)
STD = (
    "失步判据：|XACTUAL−X_ENC| ≤ 512 µsteps（encoder_tol）；电气故障位 "
    "s2g/s2vs/ol/drv_err/uv_cp = 0；过温标志 OT/OTPW = 0；看门狗零复位；"
    "SPI 无 0xFFFFFFFF 哨兵值；急停须无条件断功率。"
)
TOOLS = (
    "J-Link（烧录 + RTT + 直读 g_ag_status/BKPSRAM）；PCAN-USB + can_protocol_full.py"
    "（CAN 协议全量）；外部测温仪（TMC/MOS/DCDC 壳温）；STM32CubeCLT(gcc) 构建。"
)
STEPS = (
    "1. 固件内老化(CL_AGING_TEST=1，main 在 osKernelStart 前调用 AGING_SelfTest)："
    "15s 启动延时 → 自测 → 各档每档 60min（双机 ±1圈往复，逐腿判失步+故障，循环内喂狗）"
    "→ 急停 → LED。\n"
    "2. 自测：打印 HCLK；逐芯片读 GCONF/CHOPCONF 查 SPI 哨兵；双机 1 圈到位；编码器一致性。\n"
    "3. 每腿记录 XACTUAL/X_ENC 偏差(maxdev)与故障位；过温标志累计。\n"
    "4. 监测：外部测温仪点 TMC/MOS/DCDC 壳温；CAN 协议套件；急停物理验证。\n"
    "5. 达设定时长/中断即停；结果存后备SRAM(BKPSRAM)，插 J-Link 用 read_nvm.py 读回。"
)
DATA = (
    "一、自测 [SELFTEST]：HCLK=240MHz；U1/U2 GCONF/CHOPCONF SPI_OK；编码器偏差 "
    "U1=−54 / U2=−50 µsteps (≤512) → PASS。\n\n"
    "二、多档老化（固件内 pre-RTOS；双机开环 ±1圈往复，每档 60min；"
    "判据 |XACTUAL−X_ENC|≤512、故障位=0）：\n"
    "   档   约速度     legs     maxdev(µsteps)   结果\n"
    "   G1   约 5 rpm   312      39              PASS\n"
    "   G2   约 21 rpm  1221     39              PASS\n"
    "   G3   约 52 rpm  2899     39              PASS\n"
    "   G4   约 105 rpm 5264     64              PASS\n"
    "   G5   600 rpm    10257    90              PASS\n"
    "   G6   610 rpm    8639    29760           FAIL（约 47min 双机失步，deviation_warn 置位）\n"
    "   → G1~G5 全 PASS；最高不失步速度 = 600 rpm(G5)；610 rpm(G6) 长时必失步。\n\n"
    "三、温度（外部测温仪，老化运行中）：TMC5160 芯片 ≤80℃；板上 MOS ≤90℃；"
    "DCDC-12V ≤85℃。TMC 过温标志 OT/OTPW = 0（结温 <120℃）。\n\n"
    "四、CAN 协议：can_protocol_full v2.1 全量 22 项 PASS（含 0x09 读位置、"
    "0x07 急停非法掩码全关断）。\n\n"
    "五、急停：0x07/value=0xFF 无视电机掩码关断全部电机、回 0xE1，电机断电自由"
    "（人工手感确认）。\n\n"
    "六、结果留存：写入后备 SRAM(BKPSRAM) 环形记录，跨复位保持，可事后 read_nvm.py 读回。\n\n"
    "七、G5(600rpm) 长时复核：连续运行 142min(≈2.4h)往复 legs=24274，maxdev=64 µsteps"
    "（<512），零失步、零电气故障，全程过温标志 OT/OTPW=0；温升仍在 "
    "TMC≤80℃ / MOS≤90℃ / DCDC-12V≤85℃ 范围内。"
)
CONCL = (
    "G1~G5 长时老化零失步、零电气故障（maxdev 39~90 µsteps，远小于容差 512）；"
    "G6(610rpm) 长时失步（maxdev=29760，61µs 级失步位置位）。"
    "→ 最高不失步稳定速度 = 600 rpm（G5 生产档）。温升可控"
    "(TMC≤80℃/MOS≤90℃/DCDC-12V≤85℃，OT/OTPW=0)；SPI、编码器一致性、CAN 协议全量、"
    "急停断功率等整体通路均通过。并补充 G5(600rpm) 连续 142min 长时复核：零失步、零过温，"
    "温升仍在本报告范围内。驱动板在 G1~G5(≤600rpm) 功能与稳定性达标；"
    "G6(610rpm) 仅宜短时/有人值守使用。"
)
ADVICE = (
    "1) 生产速度上限取 600 rpm(G5)；G6(610rpm) 为极限档，长时/带载会失步，仅限短时且有"
    "人值守。\n"
    "2) 建议加装板上温度传感器（DS18B20/NTC）以自动记录绝对温度（TMC 无温度寄存器）。\n"
    "3) 建议后备 SRAM 接 VBAT，掉电不丢留存；并将 NVM 记录由 5 档扩展到 6 档（含 G6）。\n"
    "4) 反复失步后编码器基准会偏移，正式测试前应断电/回零重建零点。"
)
CONTRIB = (
    "建立板载固件内老化/整体测试框架（pre-RTOS，宏门控）+ BKPSRAM 结果留存，"
    "使长时多档测试可脱离电脑自动运行、事后读回；并测定最高不失步稳定速度 600rpm。"
)
LEFTOVER = (
    "G6(610rpm) 长时失步属已知极限（非缺陷）；绝对温度需外部/加装传感器；"
    "NVM 现只存 G1~G5（G6 待扩展到 6 档）；失步后编码器基准需断电/回零重建。"
)


def main():
    wb = openpyxl.load_workbook(SRC)
    ws = wb["测试报告"]
    ws["K2"] = "TMC5160-H7-AGE-20260930"
    ws["C3"] = "双 TMC5160 步进电机驱动板 — 固件内多档老化(G1~G6)及整体测试"
    ws["K3"] = "TMC5160 双步进电机驱动板（STM32H750VBT6 + 2×TMC5160 + 57CME13）"
    ws["E4"] = "2026.09.30"
    ws["G4"] = "约 8 小时"
    ws["K4"] = "2026.10.01"
    ws["E5"] = "2026.09.30 13:37"
    ws["G5"] = "约 8 小时（G1~G5 各 60min + G6 约47min 失步提前停）"
    ws["K5"] = "2026.10.01"
    ws["C6"] = DESC
    ws["C7"] = PURPOSE
    ws["C8"] = STD
    ws["C10"] = TOOLS
    ws["C12"] = STEPS
    ws["C18"] = DATA
    ws["C25"] = CONCL
    ws["C28"] = ADVICE
    ws["C30"] = CONTRIB
    ws["C31"] = LEFTOVER
    wb.save(DST)
    print("saved:", DST, os.path.getsize(DST), "bytes")


if __name__ == "__main__":
    main()
