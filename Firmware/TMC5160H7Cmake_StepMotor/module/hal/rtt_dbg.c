/*****************************************************************************
 * @文件: rtt_dbg.c
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: SEGGER RTT 调试输出封装（替代 CAN 调试帧 0x1AA55F44）
 * @来源: 自 TMC5160_StepMotor(F407) 移植，零芯片依赖
 * @平台: STM32H750 (Cortex-M7)
 * @依赖: SEGGER_RTT 库（SEGGER_RTT.c + SEGGER_RTT_printf.c，纯 C 实现）
 * @经验: 控制块 _SEGGER_RTT 必须被引用（RTT_DBG_Init→SEGGER_RTT_Init），
 *        否则链接器裁段导致 J-Link "Failed to find RTT control block"
 ****************************************************************************/
#include "hal/rtt_dbg.h"
#include <stdarg.h>
#include <stdio.h>

/* SEGGER RTT 头文件（库自带） */
#include "SEGGER_RTT.h"

/* ==== 接口实现 ==== */

/**
 * @说明 初始化 RTT，配置上行缓冲区（MCU→PC 方向）
 *       默认缓冲区 1024 字节，可在 RTT_Conf.h 改 SEGGER_RTT_BUFFER_SIZE_UP
 */
void RTT_DBG_Init(void)
{
    SEGGER_RTT_Init();
}

/**
 * @输入 str: '\0' 结尾文本
 * @说明 写文本到 RTT Channel 0
 */
void RTT_DBG_Str(const char *str)
{
#if RTT_DBG
    SEGGER_RTT_WriteString(RTT_DBG_CHANNEL, str);
#endif
}

/**
 * @输入 fmt: printf 格式串
 * @说明 格式化输出（复用 SEGGER_RTT_vprintf，零额外 RAM 占用）
 */
void RTT_DBG_Printf(const char *fmt, ...)
{
#if RTT_DBG
    va_list ap;
    va_start(ap, fmt);
    SEGGER_RTT_vprintf(RTT_DBG_CHANNEL, fmt, &ap);
    va_end(ap);
#endif
}

/* ==== printf retarget → RTT ==== */

/**
 * @说明 newlib-nano _write 覆盖：将标准 printf/fprintf 输出重定向到 RTT Channel 0
 *       替代 syscalls.c 中的默认实现（默认写 SWO/ITM 或无输出）
 *       受 RTT_DBG 开关控制，关闭时返回 -1（抑制输出）
 * @输入 file: 文件描述符（stdout=1, stderr=2）
 *       ptr: 数据指针; len: 数据长度
 * @输出 实际写入字节数
 */
#if RTT_DBG
int _write(int file, char *ptr, int len)
{
    (void)file;
    SEGGER_RTT_Write(RTT_DBG_CHANNEL, ptr, (unsigned)len);
    return len;
}
#else
int _write(int file, char *ptr, int len)
{
    (void)file;
    (void)ptr;
    (void)len;
    return -1;
}
#endif
