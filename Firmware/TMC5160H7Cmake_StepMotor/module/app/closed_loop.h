/*****************************************************************************
 * @文件: closed_loop.h
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: 闭环控制模块（编码器反馈 + 补步）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一，行为等价
 * @依赖: drv/tmc5160, app/motor_ctrl
 ****************************************************************************/
#ifndef CLOSED_LOOP_H
#define CLOSED_LOOP_H

#include <stdint.h>

/* ==== 闭环模式 ==== */
#define CLOSED_LOOP_OFF     0
#define CLOSED_LOOP_ON      1

/* ==== 接口 ==== */
void    CLOSEDLOOP_Init(void);
void    CLOSEDLOOP_Enable(uint8_t motor);
void    CLOSEDLOOP_Disable(uint8_t motor);
void    CLOSEDLOOP_Tick(uint8_t motor);
uint8_t CLOSEDLOOP_GetMode(uint8_t motor);
void    CLOSEDLOOP_SetTarget(uint8_t motor, int32_t target);
/* 偏差遥测：命令目标 − 编码器快照（RTT 偏差-时间曲线用；任意任务可读） */
int32_t CLOSEDLOOP_GetDeviation(uint8_t motor);

#endif /* CLOSED_LOOP_H */
