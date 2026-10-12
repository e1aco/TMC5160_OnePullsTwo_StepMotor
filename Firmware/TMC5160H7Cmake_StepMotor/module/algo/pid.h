/*****************************************************************************
 * @文件: pid.h
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: PID 控制器（纯算法，位置式）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一，行为等价
 ****************************************************************************/
#ifndef PID_H
#define PID_H

#include <stdint.h>

/* ==== 类型定义 ==== */
typedef struct {
    int32_t kp;          /* 比例系数 */
    int32_t ki;          /* 积分系数 */
    int32_t kd;          /* 微分系数 */
    int32_t integral;    /* 积分累加 */
    int32_t last_error;  /* 上次误差 */
    int32_t out_min;     /* 输出下限 */
    int32_t out_max;     /* 输出上限 */
} PID_T;

/* ==== 接口 ==== */
void    PID_Init(PID_T *pid, int32_t kp, int32_t ki, int32_t kd,
                     int32_t out_min, int32_t out_max);
void    PID_Reset(PID_T *pid);
int32_t PID_Calculate(PID_T *pid, int32_t setpoint, int32_t actual);
void    PID_SetParams(PID_T *pid, int32_t kp, int32_t ki, int32_t kd);

#endif /* PID_H */
