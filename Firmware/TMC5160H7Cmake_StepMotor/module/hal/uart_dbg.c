/*****************************************************************************
 * @文件: uart_dbg.c
 * @作者: cl
 * @日期: 2026-09-08
 * @版本: v1.0
 * @说明: USART1 调试输出封装（与 rtt_dbg 双通道并行，不互斥）
 * @依据: Core/Src/usart.c MX_USART1_UART_Init (Baud=115200, Word=8B, Stop=1, HSI 64MHz)
 *        +  串口时序: 115200 8N1 → 10bit/frame ≈ 86.8us/byte
 *        依据 .cl/memory/config.md: stm32_usart1clk=64MHz HSI
 * @注意: 发送用 HAL_UART_Transmit 阻塞式，超时 100ms；ISR 中禁止调用（阻塞）
 * @受限: 缓冲 128B static (依据 .cl/memory/ STACK_SIZE, 禁栈大缓冲) + snprintf 有界
 ****************************************************************************/
#include "hal/uart_dbg.h"
#include "usart.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>


/* ==== 接口实现 ==== */

/**
 * @说明 USART1 已在 MX_USART1_UART_Init 初始化，此处仅留痕
 */
void UART_DBG_Init(void)
{
    /* CubeMX 已初始化 huart1，无需重复 */
}

/**
 * @输入 str: '\0' 结尾文本
 * @说明 阻塞发送到 USART1 (PB14 TX)，超时 100ms
 */
void UART_DBG_Str(const char *str)
{
#if UART_DBG
    uint16_t len;
    if (NULL == str)
    {
        return;
    }
    len = (uint16_t)strlen(str);
    if (0 == len)
    {
        return;
    }
    (void)HAL_UART_Transmit(&huart1, (uint8_t *)str, len, 100);
#endif
}

/**
 * @输入 fmt: printf 格式串
 * @说明 格式化后阻塞发送，缓冲 128B static 防栈溢出
 */
void UART_DBG_Printf(const char *fmt, ...)
{
#if UART_DBG
    /* 大缓冲禁栈局部: static 128B (依据 .cl/memory/ STACK_SIZE) */
    static char s_buf[128];
    va_list ap;
    int n;

    if (NULL == fmt)
    {
        return;
    }
    va_start(ap, fmt);
    n = vsnprintf(s_buf, sizeof(s_buf), fmt, ap);
    va_end(ap);
    if (n <= 0)
    {
        return;
    }
    if (n > (int)sizeof(s_buf))
    {
        n = (int)sizeof(s_buf);
    }
    (void)HAL_UART_Transmit(&huart1, (uint8_t *)s_buf, (uint16_t)n, 100);
#endif
}
