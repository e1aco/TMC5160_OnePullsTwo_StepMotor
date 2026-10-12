/*****************************************************************************
 * @文件: pid.c
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: PID 控制器实现（纯算法，位置式 P+I+D，输出端钳位）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一，行为等价
 * @注意: 积分项无 anti-windup（仅输出端钳位），与源工程一致
 ****************************************************************************/
#include "algo/pid.h"

/* ==== 内部工具 ==== */

/**
 * @输入 value: 待限幅值; min: 下限; max: 上限
 * @输出 限幅后的值
 * @说明 将值钳制在 [min, max] 区间
 */
static int32_t S_Clamp(int32_t value, int32_t min, int32_t max)
{
    if (value < min)
    {
        return min;
    }
    if (value > max)
    {
        return max;
    }
    return value;
}

/* ==== 接口实现 ==== */

/**
 * @输入 pid: PID 句柄; kp/ki/kd: 参数; out_min/out_max: 输出限幅
 * @输出 无
 * @说明 初始化 PID，积分/上次误差清零
 */
void PID_Init(PID_T *pid, int32_t kp, int32_t ki, int32_t kd,
                  int32_t out_min, int32_t out_max)
{
    pid->kp = kp;
    pid->ki = ki;
    pid->kd = kd;
    pid->integral = 0;
    pid->last_error = 0;
    pid->out_min = out_min;
    pid->out_max = out_max;
}

/**
 * @输入 pid: PID 句柄
 * @输出 无
 * @说明 复位 PID 状态（积分清零，误差清零）
 */
void PID_Reset(PID_T *pid)
{
    pid->integral = 0;
    pid->last_error = 0;
}

/**
 * @输入 pid: PID 句柄; setpoint: 目标值; actual: 实际值
 * @输出 int32_t: PID 输出（修正量，已限幅）
 * @说明 位置式 PID：P=Kp*e, I=Ki*Σe, D=Kd*(e-e_prev)
 */
int32_t PID_Calculate(PID_T *pid, int32_t setpoint, int32_t actual)
{
    int32_t error;
    int32_t p_out, i_out, d_out, output;

    error = setpoint - actual;

    p_out = pid->kp * error;

    pid->integral += error;
    i_out = pid->ki * pid->integral;

    d_out = pid->kd * (error - pid->last_error);
    pid->last_error = error;

    output = p_out + i_out + d_out;
    output = S_Clamp(output, pid->out_min, pid->out_max);

    return output;
}

/**
 * @输入 pid: PID 句柄; kp/ki/kd: 新参数
 * @输出 无
 * @说明 在线调整 PID 参数
 */
void PID_SetParams(PID_T *pid, int32_t kp, int32_t ki, int32_t kd)
{
    pid->kp = kp;
    pid->ki = ki;
    pid->kd = kd;
}

