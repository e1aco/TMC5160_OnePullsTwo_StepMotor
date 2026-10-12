/*****************************************************************************
 * @文件: closed_loop.c
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: 闭环控制模块（编码器反馈 + PID 补步）
 *        2026-09-24：PID 接入回路——Tick 用 PID_Calculate 算补步量；
 *        按电机双 PID 实例；内部修正不复位 PID（积分/微分跨修正周期累积），
 *        外部新命令（SetTarget）才复位；容差死区保留（死区内不推进 PID，
 *        防噪声下积分饱和）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一
 * @依赖: drv/tmc5160, app/motor_ctrl, app/sched, algo/pid, app/homing
 ****************************************************************************/
#include "app/closed_loop.h"
#include "algo/pid.h"
#include "app/motor_ctrl.h"
#include "app/sched.h"
#include "app/homing.h"
#include "drv/tmc5160.h"

/* ==== 内部常量 ==== */
/* 默认 PID 参数（保守值，首次测试用；整定后改） */
#define CL_PID_KP_DEFAULT      5
#define CL_PID_KI_DEFAULT      0
#define CL_PID_KD_DEFAULT      0

/* 输出限幅（每周期最大修正量） */
#define CL_PID_OUT_MIN         (-200)
#define CL_PID_OUT_MAX         200

/* ==== 内部变量 ==== */
/* 命令目标位置（用户要求的位置，闭环对比基准） */
static int32_t s_cmd_target[2];

/* 按电机 PID 实例（下标 0=U1，1=U2；与 s_cmd_target 对齐） */
static PID_T s_pid_st[2];

/* ==== 内部工具 ==== */

/**
 * @输入 motor: 电机编号(U1/U2); target: 内部修正目标
 * @输出 无
 * @说明 PID 内部修正落点：只改命令目标，**不复位 PID**
 *        （与 CLOSEDLOOP_SetTarget 的区别：后者是外部新命令，复位 PID）
 */
static void S_SetCorrection(uint8_t motor, int32_t target)
{
    int32_t idx = (MOTOR_CTRL_U1 == motor) ? 0 : 1;

    s_cmd_target[idx] = target;
}

/* ==== 接口实现 ==== */

/**
 * @输入 无
 * @输出 无
 * @说明 初始化闭环 PID 参数与命令目标（双电机实例）
 */
void CLOSEDLOOP_Init(void)
{
    uint8_t i;

    for (i = 0U; i < 2U; i++)
    {
        PID_Init(&s_pid_st[i],
                 CL_PID_KP_DEFAULT, CL_PID_KI_DEFAULT, CL_PID_KD_DEFAULT,
                 CL_PID_OUT_MIN, CL_PID_OUT_MAX);
    }
    s_cmd_target[0] = 0;
    s_cmd_target[1] = 0;
}

/**
 * @输入 motor: 电机编号; target: 命令目标位置
 * @输出 无
 * @说明 设置闭环命令基准目标（外部新命令），由电机控制层在发出运动指令
 *        时调用；同时复位该电机 PID（积分/微分清零，防跨命令累积）
 */
void CLOSEDLOOP_SetTarget(uint8_t motor, int32_t target)
{
    if (MOTOR_CTRL_U1 == motor)
    {
        s_cmd_target[0] = target;
        PID_Reset(&s_pid_st[0]);
    }
    else if (MOTOR_CTRL_U2 == motor)
    {
        s_cmd_target[1] = target;
        PID_Reset(&s_pid_st[1]);
    }
}

/**
 * @输入 motor: 电机编号
 * @输出 无
 * @说明 使能闭环控制，已使能时跳过
 * @注意 状态即时生效（RAM 常驻）
 */
void CLOSEDLOOP_Enable(uint8_t motor)
{
    TMC5160_CHIP_T *chip = MOTOR_GetChip(motor);
    if ((void *)0 != chip)
    {
        if (CLOSED_LOOP_ON == chip->closed_loop)
        {
            return;
        }
        chip->closed_loop = CLOSED_LOOP_ON;
    }
    if ((MOTOR_CTRL_U1 == motor) || (MOTOR_CTRL_U2 == motor))
    {
        PID_Reset(&s_pid_st[(MOTOR_CTRL_U1 == motor) ? 0 : 1]);
    }
}

/**
 * @输入 motor: 电机编号
 * @输出 无
 * @说明 禁用闭环控制，已禁用时跳过
 * @注意 状态即时生效（RAM 常驻）
 */
void CLOSEDLOOP_Disable(uint8_t motor)
{
    TMC5160_CHIP_T *chip = MOTOR_GetChip(motor);
    if ((void *)0 != chip)
    {
        if (CLOSED_LOOP_OFF == chip->closed_loop)
        {
            return;
        }
        chip->closed_loop = CLOSED_LOOP_OFF;
    }
}

/**
 * @输入 motor: 电机编号
 * @输出 uint8_t: 闭环模式状态
 */
uint8_t CLOSEDLOOP_GetMode(uint8_t motor)
{
    TMC5160_CHIP_T *chip = MOTOR_GetChip(motor);
    if ((void *)0 != chip)
    {
        return chip->closed_loop;
    }
    return CLOSED_LOOP_OFF;
}

/**
 * @输入 motor: 电机编号
 * @输出 int32_t: 命令目标 − 编码器快照（偏差遥测，RTT 偏差-时间曲线用）
 */
int32_t CLOSEDLOOP_GetDeviation(uint8_t motor)
{
    int32_t idx = (MOTOR_CTRL_U1 == motor) ? 0 : 1;

    return s_cmd_target[idx] - SCHED_GetEncoder(motor);
}

/**
 * @输入 motor: 电机编号
 * @输出 无
 * @说明 闭环控制周期（外部节拍或主循环调用）
 *   1. 等待斜坡完成（XACTUAL == 命令目标）后才介入
 *   2. 读取编码器(X_ENC)，偏差超容差 → PID 算补步量
 *   3. 补步落盘 + 更新命令目标（内部修正，不复位 PID）
 * @注意 不干涉正在运行的斜坡，防止目标跑飞；
 *   回零/校准中跳过（防 RAMPMODE 劫持）；
 *   容差死区内不推进 PID（防噪声下积分饱和）
 */
void CLOSEDLOOP_Tick(uint8_t motor)
{
    TMC5160_CHIP_T *chip;
    int32_t x_actual, x_enc, cmd_target, deviation, new_target, corr;
    int32_t idx;

    chip = MOTOR_GetChip(motor);
    if ((void *)0 == chip)
    {
        return;
    }

    if (CLOSED_LOOP_OFF == chip->closed_loop)
    {
        return;
    }

    /* 回零/校准中：不干涉速度模式（补步会写 RAMPMODE=0 劫持回零） */
    if (0U != HOME_IsCalibrating(MOTOR_MASK_BIT(motor)))
    {
        return;
    }

    /* 取命令目标（固定值） */
    idx = (MOTOR_CTRL_U1 == motor) ? 0 : 1;
    cmd_target = s_cmd_target[idx];

    /* 斜坡未完成 → 不干涉，让 TMC5160 自行到位 */
    x_actual = SCHED_GetActual(motor);
    if (x_actual != cmd_target)
    {
        return;
    }

    /* 读编码器快照，算偏差 */
    x_enc = SCHED_GetEncoder(motor);
    deviation = cmd_target - x_enc;

    /* 偏差在容差内 → 不动（死区，PID 状态保持） */
    if (deviation > -TMC5160_ENC_TOLERANCE &&
        deviation <  TMC5160_ENC_TOLERANCE)
    {
        return;
    }

    /* 偏差超出容差 → PID 算补步量并直接落盘（同 senseTask 上下文） */
    corr = PID_Calculate(&s_pid_st[idx], cmd_target, x_enc);
    new_target = cmd_target + corr;
    S_SetCorrection(motor, new_target);
    TMC5160_MoveTo(chip, new_target);
}
