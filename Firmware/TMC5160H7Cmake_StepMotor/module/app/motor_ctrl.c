/*****************************************************************************
 * @文件: motor_ctrl.c
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: 电机控制中间层（封装电机选择 + 运动执行）
 *        2026-09-23 摆法2：仅任务A（senseTask）调用，函数体直调 drv 原语
 *        + 闭环目标同步（删服务 FIFO 间接层；禁其他任务调用）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一，行为等价（纯逻辑零适配）
 * @依赖: drv/tmc5160(查表), app/closed_loop(RAM 目标)
 ****************************************************************************/
#include "app/motor_ctrl.h"
#include "drv/tmc5160.h"
#include "app/closed_loop.h"

/* ==== 内部工具 ==== */

/**
 * @输入 motor: 电机编号（MOTOR_CTRL_Ux，与芯片表 id 数值对应）
 * @输出 芯片表项指针，无匹配返 NULL
 * @说明 id→表项唯一转换点（drv TMC5160_Lookup 查单表）
 */
TMC5160_CHIP_T *MOTOR_GetChip(uint8_t motor)
{
    return TMC5160_Lookup(motor);
}

/* ==== 接口实现 ==== */

/**
 * @输入 无
 * @输出 无
 * @说明 电机控制模块初始化（实际由 TMC5160_Init 完成）
 */
void MOTOR_Init(void)
{
    /* 初始化由 TMC5160_Init() 完成，此处无需额外操作 */
}

/**
 * @输入 motor: 电机掩码; target: 目标绝对位置
 * @输出 无
 * @说明 掩码内逐电机直调执行（任务A 上下文，SPI 合法）+ 闭环目标同步
 */
void MOTOR_MoveTo(uint8_t motor, int32_t target)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (0U != (motor & MOTOR_MASK_BIT(chip->id))))
        {
            TMC5160_MoveTo(chip, target);
            CLOSEDLOOP_SetTarget(chip->id, target);
        }
    }
}

/**
 * @输入 motor: 电机掩码; offset: 相对偏移量(+正转, -反转)
 * @输出 无
 * @说明 掩码内逐电机直调执行；闭环目标用执行前 XACTUAL+offset
 */
void MOTOR_MoveBy(uint8_t motor, int32_t offset)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (0U != (motor & MOTOR_MASK_BIT(chip->id))))
        {
            int32_t base = TMC5160_GetPosition(chip);

            TMC5160_MoveBy(chip, offset);
            CLOSEDLOOP_SetTarget(chip->id, base + offset);
        }
    }
}

/**
 * @输入 motor: 电机掩码; velocity: 目标速度(+正转, -反转)
 * @输出 无
 * @说明 掩码内逐电机直调执行（速度模式无固定目标，不同步闭环 target）
 */
void MOTOR_SetVelocity(uint8_t motor, int32_t velocity)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (0U != (motor & MOTOR_MASK_BIT(chip->id))))
        {
            TMC5160_SetVelocity(chip, velocity);
        }
    }
}

/**
 * @输入 motor: 电机掩码
 * @输出 无
 * @说明 掩码内逐电机停止：停止即以当前位置为闭环目标
 */
void MOTOR_Stop(uint8_t motor)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (0U != (motor & MOTOR_MASK_BIT(chip->id))))
        {
            int32_t p = TMC5160_GetPosition(chip);

            TMC5160_Stop(chip);
            CLOSEDLOOP_SetTarget(chip->id, p);
        }
    }
}

/**
 * @输入 motor: 电机掩码
 * @输出 无
 * @说明 掩码内逐电机急停关断功率级（不锁轴；位置丢失，再运动前须重回零）
 */
void MOTOR_EStop(uint8_t motor)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (0U != (motor & MOTOR_MASK_BIT(chip->id))))
        {
            TMC5160_EStop(chip);
        }
    }
}

/**
 * @输入 motor: 电机掩码; group: 运动参数组 ID(1~5)
 * @输出 无
 * @说明 掩码内逐电机应用速度/加速度剖面
 */
void MOTOR_ApplyProfile(uint8_t motor, uint8_t group)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) && (0U != (motor & MOTOR_MASK_BIT(chip->id))))
        {
            TMC5160_ApplyProfile(chip, group);
        }
    }
}

/* 读接口已删（摆法2）：ACK/终态改走 app/sched 快照，任务A 直读 SPI 原语 */

