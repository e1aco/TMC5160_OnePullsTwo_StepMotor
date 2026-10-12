/*****************************************************************************
 * @文件: sched.c
 * @作者: cl
 * @日期: 2026-09-22
 * @版本: v1.0
 * @说明: RTOS 任务间数据交换层实现（SPI 采集快照）
 * @依据: RTOS 移植裁决 2026-09-22（方案 c）+ 2026-09-23 摆法2
 * @依赖: drv/tmc5160, app/motor_ctrl
 ****************************************************************************/
#include "app/sched.h"
#include "app/motor_ctrl.h"
#include "drv/tmc5160.h"
#include "tim_test.h"   /* TEST_TIM_* 探针宏: 宏版实测/生产版空宏零开销 (probe.md) */

/* ==== 内部状态 ==== */

static SCHED_SNAP_T s_snap[SCHED_MOTOR_COUNT];

/* 回零兴趣位（下标=快照槽；1=回零期，ramp/drv/gstat 随快采 2ms 采集） */
static uint8_t s_home_interest[SCHED_MOTOR_COUNT];

/* ==== 内部工具 ==== */

/**
 * @输入 motor: 电机编号（= 芯片表 id）
 * @输出 槽位下标，非法返回 -1
 * @说明 经芯片表比对 id（单源 TMC5160_ChipAt）
 */
static int S_Idx(uint8_t motor)
{
    uint8_t i;

    for (i = 0U; i < SCHED_MOTOR_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (chip->id == motor))
        {
            return (int)i;
        }
    }
    return -1;
}

/* ==== 接口实现 ==== */

void SCHED_Init(void)
{
    uint8_t i;

    for (i = 0U; i < SCHED_MOTOR_COUNT; i++)
    {
        s_snap[i].x_actual   = 0;
        s_snap[i].x_enc      = 0;
        s_snap[i].vactual    = 0;
        s_snap[i].ramp_stat  = 0U;
        s_snap[i].drv_status = 0U;
        s_snap[i].gstat      = 0U;
        s_snap[i].enc_status = 0U;
        s_snap[i].diag_valid = 0U;
        s_home_interest[i] = 0U;
    }
}

void SCHED_CollectFast(void)
{
    uint8_t i;
#ifdef CL_TIMING_MEASURE
    uint8_t round;
#endif

    TEST_TIM_Start(3);   /* tag3=快照全窗; 判据: XACTUAL→X_ENC=紧邻1读22us, Σ见 require.md */
#ifdef CL_TIMING_MEASURE
    /* 时序压测：双芯片完整快采×3（3轮×2芯片×3寄存器=18次SPI读），
     * 模拟多次读取下的 tag3/tag2/Σ 门禁；生产版单轮不变 */
    for (round = 0U; round < 3U; round++)
#endif
    {
        for (i = 0U; i < SCHED_MOTOR_COUNT; i++)
        {
            TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

            if ((void *)0 == chip)
            {
                continue;
            }
            s_snap[i].x_actual = TMC5160_GetPosition(chip);
            s_snap[i].x_enc    = TMC5160_GetEncoderPosition(chip);
            /* vactual 入快采：到位判定需速度归零（摆法2 任务B纯读快照） */
            s_snap[i].vactual  = TMC5160_GetVelocity(chip);
            /* 回零兴趣：状态机判据寄存器随快采 2ms 采集（回零期零直读） */
            if (0U != s_home_interest[i])
            {
                s_snap[i].ramp_stat  = TMC5160_GetRampStat(chip);
                s_snap[i].drv_status = TMC5160_GetDrvStatus(chip);
                s_snap[i].gstat      = TMC5160_GetGStat(chip);
                s_snap[i].diag_valid = 1U;
            }
        }
    }
    TEST_TIM_Stop(3);
}

void SCHED_CollectDiag(void)
{
    uint8_t i;

    for (i = 0U; i < SCHED_MOTOR_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if ((void *)0 == chip)
        {
            continue;
        }
        /* vactual 已迁 CollectFast；诊断块只采状态/保护寄存器 */
        s_snap[i].ramp_stat  = TMC5160_GetRampStat(chip);
        s_snap[i].drv_status = TMC5160_GetDrvStatus(chip);
        s_snap[i].gstat      = TMC5160_GetGStat(chip);
        s_snap[i].enc_status = TMC5160_GetEncStatus(chip);
        s_snap[i].diag_valid = 1U;
    }
}

void SCHED_SetHoming(uint8_t motor, uint8_t on)
{
    uint8_t i;

    for (i = 0U; i < SCHED_MOTOR_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (0U != (motor & MOTOR_MASK_BIT(chip->id))))
        {
            s_home_interest[i] = (0U != on) ? 1U : 0U;
        }
    }
}

const SCHED_SNAP_T *SCHED_Snap(uint8_t motor)
{
    int idx = S_Idx(motor);

    if (idx < 0)
    {
        return (void *)0;
    }
    return &s_snap[idx];
}

int32_t SCHED_GetActual(uint8_t motor)
{
    int idx = S_Idx(motor);

    if (idx < 0)
    {
        return 0;
    }
    return s_snap[idx].x_actual;
}

int32_t SCHED_GetEncoder(uint8_t motor)
{
    int idx = S_Idx(motor);

    if (idx < 0)
    {
        return 0;
    }
    return s_snap[idx].x_enc;
}
