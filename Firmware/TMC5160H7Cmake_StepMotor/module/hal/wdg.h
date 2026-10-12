/*****************************************************************************
 * @文件: wdg.h
 * @作者: cl
 * @日期: 2026-09-20
 * @版本: v1.0
 * @说明: 独立看门狗 IWDG 驱动层（HAL 封装；启动由 CubeMX MX_IWDG1_Init 完成）
 * @平台: STM32H750VBT6 (IWDG1, /128 + 1249 ≈ 5s，见 Core/Src/iwdg.c)
 * @依赖: HAL_IWDG, Core/Src/iwdg.c hiwdg1, config.h WDG_ENABLE 开关
 ****************************************************************************/
#ifndef WDG_H
#define WDG_H

#include <stdint.h>

/* 超时配比归 CubeMX（Core/Src/iwdg.c: Prescaler=/128 + Reload=1249 ≈ 5s）；
 * 本层不存 PR/RLR，避免双源（r3 前车：寄存器直写与 CubeMX 二选一，已选 HAL） */

/* 初始化：锁存复位源→打印→清标志→启动 IWDG（须 RTT/UART 就绪后调用） */
void WDG_Init(void);

/* 喂狗（重装载计数器） */
void WDG_Feed(void);

/* 本次上电是否为 IWDG 复位（卡死验证判据；1=是） */
uint8_t WDG_WasIwdgReset(void);

/* IWDG 启动同步诊断标志（1=WDG_Init 时 PVU/RVU/WVU 全清；0=同步告警）
 * 2026-09-22：不再门控喂狗（MX 已启动 IWDG，停喂=复位环），仅供诊断 */
uint8_t WDG_IsOn(void);

/* 本次上电会话的复位次数（RTC 备份寄存器 BKP0R 跨系统复位持久；
 * POR 首启=1，之后每次系统复位 +1；供老化全程统计复位次数） */
uint32_t WDG_BootCount(void);

#endif /* WDG_H */
