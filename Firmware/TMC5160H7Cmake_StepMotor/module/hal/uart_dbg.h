/*****************************************************************************
 * @文件: uart_dbg.h
 * @作者: cl
 * @日期: 2026-09-08
 * @版本: v1.0
 * @说明: USART1 调试输出封装（PB14 TX / PB15 RX, 115200 8N1）
 * @依据: Core/Src/usart.c MX_USART1_UART_Init (Baud=115200, HSI 64MHz)
 *        + require.md 引脚映射 PB14=USART1_TX/PB15=USART1_RX
 * @依赖: HAL_UART (huart1) + config.h UART_DBG 通道开关（.c 内按 #if 剥实现）
 ****************************************************************************/
#ifndef UART_DBG_H
#define UART_DBG_H

#include <stdint.h>
#include "config.h"

void UART_DBG_Init(void);
void UART_DBG_Str(const char *str);
void UART_DBG_Printf(const char *fmt, ...);

#endif /* UART_DBG_H */
