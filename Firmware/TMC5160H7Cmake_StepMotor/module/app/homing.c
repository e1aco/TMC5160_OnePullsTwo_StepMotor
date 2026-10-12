/*****************************************************************************
 * @文件: homing.c
 * @作者: cl
 * @日期: 2026-09-12
 * @版本: v1.0
 * @说明: 无编码器回零/校准编排层（StallGuard2 堵转检测碰硬限位，不用编码器）
 *        标准流程依据 ch13 §13.4：硬限位方向速度运动 → 堵转 →
 *        反向回退防卡死 → XACTUAL 写 0 标定零点
 *        2026-09-24 重构：①读全部走 app/sched 快照（回零期零 SPI 直读，
 *        RAMP_STAT/DRVSTATUS/XACTUAL 由 IF_HOMING 门控 2ms 采集）；
 *        ②按电机数组化，支持位掩码并发回零；③电机级校准状态
 * @依赖: drv/tmc5160(SG 原语/写), app/sched(快照), app/motor_ctrl(掩码),
 *        app/closed_loop(目标同步), drv/can(终态/错误帧), main.h(HAL_GetTick)
 ****************************************************************************/
#include "app/homing.h"
#include "app/motor_ctrl.h"
#include "app/closed_loop.h"
#include "app/sched.h"
#include "drv/tmc5160.h"
#include "hal/can.h"
#include "main.h"

/* ==== 确认与超时 ==== */
/* SG 轮询周期: fullstep 周期@2RPS=1/(2×200)=2.5ms, 取 2ms 覆盖每 fullstep
 * 依据 .cl/memory/config.md home_sg_poll_ms=2 */
#define HOME_SG_POLL_MS    2U
/* 连续零确认次数（用户 2026-09-12 定） */
#define HOME_SG_CONFIRM    8U
/* 反向回退步数（用户 2026-09-12 定） */
#define HOME_BO_STEPS      2048L
/* 寻零超时：机械行程未知，2RPS 下 30s=60 转上限兜底 */
#define HOME_RUN_TIMEOUT   30000U
/* 回退到位超时：10s 兜底堵死 */
#define HOME_BO_TIMEOUT    10000U

/* ==== 内部状态（按芯片表下标；s_state 跨任务查询读） ==== */
static volatile HOME_STATE_T s_state[TMC5160_CHIP_COUNT];
static int8_t   s_seek_sign[TMC5160_CHIP_COUNT];   /* +1 正向 / -1 负向 */
static uint8_t  s_confirm[TMC5160_CHIP_COUNT];
static uint8_t  s_stable[TMC5160_CHIP_COUNT];
static uint8_t  s_param[TMC5160_CHIP_COUNT];       /* 回显参数组（到位反馈 Can[2]） */
static uint32_t s_last_poll[TMC5160_CHIP_COUNT];
static uint32_t s_t_mark[TMC5160_CHIP_COUNT];
static int32_t  s_bo_target[TMC5160_CHIP_COUNT];

/* ==== 内部工具 ==== */

/**
 * @输入 motor: 电机编号(bit 位号 i → 电机 i+1)
 * @输出 芯片表下标，非法返 -1
 */
static int S_Idx(uint8_t motor)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (chip->id == motor))
        {
            return (int)i;
        }
    }
    return -1;
}

/**
 * @输入 idx: 芯片下标; ok: 1=成功(已校准)，0=失败/超时(未校准)
 * @输出 无
 * @说明 进入终态：停车→恢复现场→发 CAN（成功到位反馈 / 失败未校准错误帧）
 *        成功：HomeZero 标定 → 闭环目标 0；失败：闭环目标取快照位置
 */
static void S_Finish(uint8_t idx, uint8_t ok)
{
    TMC5160_CHIP_T *chip = TMC5160_ChipAt(idx);
    const SCHED_SNAP_T *snap;

    if ((void *)0 == chip)
    {
        return;
    }
    snap = SCHED_Snap(chip->id);

    if (0U != ok)
    {
        uint8_t status;

        TMC5160_HomeZero(chip);
        TMC5160_Stop(chip);
        CLOSEDLOOP_SetTarget(chip->id, 0);
        TMC5160_HomeRestore(chip);
        SCHED_SetHoming(chip->id, 0U);
        s_state[idx] = HOME_ST_CALIBRATED;
        /* 回零到位帧状态位：非破坏直读 + 到位强制 1（d3..d6 同 can.c 组帧） */
        status = TMC5160_ReadStatusFlags(chip);
        status = (uint8_t)((status & (uint8_t)(~CAN_ST_ARRIVED)) |
                           CAN_ST_ARRIVED);
        if (0U != (status & CAN_ST_DRVERR))
        {
            (void)TMC5160_SpiWrite(chip, REG_GSTAT, 0x02U);
        }
        (void)CAN_SendArrivalRaw(CMD_HOME, chip->id, s_param[idx], status);
    }
    else
    {
        TMC5160_Stop(chip);
        CLOSEDLOOP_SetTarget(chip->id,
                             ((void *)0 != snap) ? snap->x_actual : 0);
        TMC5160_HomeRestore(chip);
        SCHED_SetHoming(chip->id, 0U);
        s_state[idx] = HOME_ST_UNCALIBRATED;
        (void)CAN_SendErrorRaw(CAN_ERR_UNCALIBRATED);
    }
}

/**
 * @输入 idx: 芯片下标
 * @输出 无
 * @说明 堵转确认后转入回退段：停车→反向 MoveTo(快照位置+回退量)→登记目标
 *        单源：MoveTo 目标与 s_bo_target 同值（到位判定严格相等）
 */
static void S_EnterBackoff(uint8_t idx)
{
    TMC5160_CHIP_T *chip = TMC5160_ChipAt(idx);
    const SCHED_SNAP_T *snap;
    int32_t backoff;
    int32_t target;

    if ((void *)0 == chip)
    {
        S_Finish(idx, 0U);
        return;
    }
    snap = SCHED_Snap(chip->id);
    TMC5160_Stop(chip);
    target = ((void *)0 != snap) ? snap->x_actual : 0;
    backoff = (0 < s_seek_sign[idx]) ? -HOME_BO_STEPS : HOME_BO_STEPS;
    target += backoff;
    TMC5160_MoveTo(chip, target);
    CLOSEDLOOP_SetTarget(chip->id, target);
    s_bo_target[idx] = target;
    s_stable[idx] = 0;
    s_t_mark[idx] = HAL_GetTick();
    s_state[idx] = HOME_ST_BACKOFF;
}

/* ==== 接口实现 ==== */

void HOME_Init(void)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        s_state[i] = HOME_ST_UNCALIBRATED;
        s_seek_sign[i] = 0;
        s_confirm[i] = 0;
        s_stable[i] = 0;
        s_param[i] = 0U;
        s_last_poll[i] = 0U;
        s_t_mark[i] = 0U;
        s_bo_target[i] = 0;
    }
}

void HOME_ZeroAtPowerUp(void)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if ((void *)0 == chip)
        {
            continue;
        }
        TMC5160_ZeroPosition(chip);      /* 上电位置设为零点 */
        CLOSEDLOOP_SetTarget(chip->id, 0);
        s_state[i] = HOME_ST_CALIBRATED;
    }
}

uint8_t HOME_Start(uint8_t motor_mask, uint8_t dir, uint8_t param)
{
    uint8_t i;
    uint8_t started = 0U;

    if ((TMC5160_HOME_DIR_POSITIVE != dir) &&
        (TMC5160_HOME_DIR_NEGATIVE != dir))
    {
        return 1U;
    }
    /* 忙检查：任一所选在 RUN/BACKOFF → 整体拒绝（原子） */
    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) &&
            (0U != (motor_mask & MOTOR_MASK_BIT(chip->id))) &&
            ((HOME_ST_RUN == s_state[i]) || (HOME_ST_BACKOFF == s_state[i])))
        {
            return 1U;
        }
    }
    /* 启动掩码内各电机（同一任务A调用内连续 SPI，µs 级准同时） */
    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 == chip) ||
            (0U == (motor_mask & MOTOR_MASK_BIT(chip->id))))
        {
            continue;
        }
        s_seek_sign[i] = (TMC5160_HOME_DIR_POSITIVE == dir) ? 1 : -1;
        s_confirm[i] = 0U;
        s_stable[i] = 0U;
        s_param[i] = param;
        TMC5160_HomeClearStall(chip);   /* 启动前清锁存，防跨次污染 */
        TMC5160_HomeConfig(chip);
        if (0 < s_seek_sign[i])
        {
            TMC5160_SetVelocity(chip, (int32_t)TMC5160_HOME_VMAX);
        }
        else
        {
            TMC5160_SetVelocity(chip, -(int32_t)TMC5160_HOME_VMAX);
        }
        s_last_poll[i] = HAL_GetTick();
        s_t_mark[i] = s_last_poll[i];
        s_state[i] = HOME_ST_RUN;
        SCHED_SetHoming(chip->id, 1U);
        started = 1U;
    }
    return (0U != started) ? 0U : 1U;
}

void HOME_Tick(void)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip;
        const SCHED_SNAP_T *snap;
        uint32_t now;

        if ((HOME_ST_RUN != s_state[i]) && (HOME_ST_BACKOFF != s_state[i]))
        {
            continue;
        }
        chip = TMC5160_ChipAt(i);
        if ((void *)0 == chip)
        {
            S_Finish(i, 0U);
            continue;
        }
        snap = SCHED_Snap(chip->id);
        if ((void *)0 == snap)
        {
            S_Finish(i, 0U);
            continue;
        }
        now = HAL_GetTick();

        if (HOME_ST_RUN == s_state[i])
        {
            if ((now - s_t_mark[i]) >= HOME_RUN_TIMEOUT)
            {
                S_Finish(i, 0U);
                continue;
            }
            if ((now - s_last_poll[i]) < HOME_SG_POLL_MS)
            {
                continue;
            }
            s_last_poll[i] = now;

            /* 硬件兜底：快照 RAMP_STAT.event_stop_sg（检测到即清锁存） */
            if (0UL != (snap->ramp_stat & TMC5160_RAMP_EVENT_STOP_SG))
            {
                TMC5160_HomeClearStall(chip);
                S_EnterBackoff(i);
                continue;
            }
            /* 主判据：SG_RESULT 连续零确认（快照 drv_status[9:0]） */
            if (0U == (snap->drv_status & TMC5160_SG_RESULT_MASK))
            {
                s_confirm[i]++;
                if (s_confirm[i] >= HOME_SG_CONFIRM)
                {
                    S_EnterBackoff(i);
                    continue;
                }
            }
            else
            {
                s_confirm[i] = 0U;
            }
        }
        else /* HOME_ST_BACKOFF */
        {
            if ((now - s_t_mark[i]) >= HOME_BO_TIMEOUT)
            {
                S_Finish(i, 0U);
                continue;
            }
            if ((now - s_last_poll[i]) < 10U)
            {
                continue;
            }
            s_last_poll[i] = now;

            /* 到位轮询：快照 XACTUAL 连续 2 次 == 目标 */
            if (s_bo_target[i] == snap->x_actual)
            {
                s_stable[i]++;
                if (s_stable[i] >= 2U)
                {
                    S_Finish(i, 1U);
                }
            }
            else
            {
                s_stable[i] = 0U;
            }
        }
    }
}

void HOME_Abort(uint8_t motor_mask)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 == chip) ||
            (0U == (motor_mask & MOTOR_MASK_BIT(chip->id))))
        {
            continue;
        }
        if ((HOME_ST_RUN == s_state[i]) || (HOME_ST_BACKOFF == s_state[i]))
        {
            TMC5160_Stop(chip);
            TMC5160_HomeRestore(chip);
            SCHED_SetHoming(chip->id, 0U);
            s_state[i] = HOME_ST_UNCALIBRATED;
        }
    }
}

void HOME_MarkUncalibrated(uint8_t motor_mask)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 == chip) ||
            (0U == (motor_mask & MOTOR_MASK_BIT(chip->id))))
        {
            continue;
        }
        if ((HOME_ST_RUN == s_state[i]) || (HOME_ST_BACKOFF == s_state[i]))
        {
            /* 急停自行关断功率级，这里只恢复寄存器现场/清兴趣 */
            TMC5160_HomeRestore(chip);
            SCHED_SetHoming(chip->id, 0U);
        }
        s_state[i] = HOME_ST_UNCALIBRATED;
    }
}

uint8_t HOME_IsCalibrating(uint8_t motor_mask)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) &&
            (0U != (motor_mask & MOTOR_MASK_BIT(chip->id))) &&
            ((HOME_ST_RUN == s_state[i]) || (HOME_ST_BACKOFF == s_state[i])))
        {
            return 1U;
        }
    }
    return 0U;
}

uint8_t HOME_IsUncalibrated(uint8_t motor_mask)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) &&
            (0U != (motor_mask & MOTOR_MASK_BIT(chip->id))) &&
            (HOME_ST_UNCALIBRATED == s_state[i]))
        {
            return 1U;
        }
    }
    return 0U;
}

uint8_t HOME_IsCalibrated(uint8_t motor_mask)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) &&
            (0U != (motor_mask & MOTOR_MASK_BIT(chip->id))) &&
            (HOME_ST_CALIBRATED != s_state[i]))
        {
            return 0U;
        }
    }
    return 1U;
}

HOME_STATE_T HOME_GetState(uint8_t motor)
{
    int idx = S_Idx(motor);

    if (idx < 0)
    {
        return HOME_ST_UNCALIBRATED;
    }
    return s_state[idx];
}
