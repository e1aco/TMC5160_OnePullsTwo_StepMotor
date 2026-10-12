/*****************************************************************************
 * @文件: rtt_dbg.h
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: SEGGER RTT 调试输出封装（替代 CAN 调试帧）
 * @来源: 自 TMC5160_StepMotor(F407) 移植，零芯片依赖
 * @依赖: SEGGER_RTT 库（SEGGER_RTT/RTT 目录源文件）+ config.h RTT_DBG 通道开关
 ****************************************************************************/
#ifndef RTT_DBG_H
#define RTT_DBG_H

#include <stdint.h>
#include "config.h"

/* RTT 通道定义 */
#define RTT_DBG_CHANNEL     0   /* Terminal 0（RTT Viewer 默认通道） */

/* ==== 接口 ==== */

/**
 * @说明 初始化 RTT 控制块，配置上行缓冲区大小
 *       在 main() 初始化段调用一次
 */
void RTT_DBG_Init(void);

/**
 * @输入 str: '\0' 结尾的文本
 * @说明 写文本到 RTT 环形缓冲区（非阻塞，后台由 J-Link 读走）
 */
void RTT_DBG_Str(const char *str);

/**
 * @输入 fmt: printf 格式串; ...: 可变参数
 * @说明 格式化输出到 RTT（SEGGER_RTT_vprintf 实现）
 */
void RTT_DBG_Printf(const char *fmt, ...);

#endif /* RTT_DBG_H */
