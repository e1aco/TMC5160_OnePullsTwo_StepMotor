/*****************************************************************************
 * @文件: can_cmd.c
 * @作者: cl
 * @日期: 2026-09-23
 * @版本: v1.0
 * @说明: CAN 命令分发与反馈组装实现（用户协议 v2.1，见 require.md 附录A）
 *        命令帧 Can[0]=cmd/Can[1]=motor 位掩码/Can[2]=param/Can[3..6]=value 大端；
 *        反馈：ACK（命令码回显）、到位反馈（cmd/motor/param/到位/过温/
 *        驱动异常/状态位）、位置反馈（0x09：cmd/motor/状态位/pos 大端）、
 *        错误帧（Can[0..6] 全=错误码）
 *        摆法2：解析一次成结构体 → 就地执行 → 即时反馈；仅任务A调用
 * @依赖: drv/can, drv/tmc5160, app/motor_ctrl, app/closed_loop,
 *        app/homing, app/sense, app/sched, algo/queue
 ****************************************************************************/
#include "app/can_cmd.h"
#include "hal/can.h"
#include "drv/tmc5160.h"
#include "algo/queue.h"
#include "app/motor_ctrl.h"
#include "app/closed_loop.h"
#include "app/homing.h"
#include "app/sense.h"

/* ==== 内部工具 ==== */

/**
 * @输入 data: 4字节大端数据(Can[3..6])
 * @输出 int32_t: 解析后的有符号值
 */
static int32_t S_BytesToInt32BE(uint8_t *data)
{
    int32_t val;

    val  = (int32_t)data[0] << 24;
    val |= (int32_t)data[1] << 16;
    val |= (int32_t)data[2] << 8;
    val |= (int32_t)data[3];
    return val;
}

/**
 * @输入 motor: 电机掩码(bit(i)=电机i+1)
 * @输出 无
 * @说明 在线清零掩码内各路位置计数器 + 闭环目标同步归零（仅任务A调用）
 */
static void S_ZeroCounters(uint8_t motor)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if (((void *)0 != chip) &&
            (0U != (motor & MOTOR_MASK_BIT(chip->id))))
        {
            TMC5160_ZeroPosition(chip);
            CLOSEDLOOP_SetTarget(chip->id, 0);
        }
    }
}

/**
 * @输入 cmd: 命令码
 * @输出 1=该命令使用 motor 字段（需掩码校验）
 */
static uint8_t S_CmdUsesMotor(uint8_t cmd)
{
    return (uint8_t)((CMD_ABS_POS == cmd) || (CMD_REL_CW == cmd) ||
                     (CMD_REL_CCW == cmd) || (CMD_VELOCITY == cmd) ||
                     (CMD_HOME == cmd) || (CMD_CL_SET == cmd) ||
                     (CMD_STOP_ESTOP == cmd) || (CMD_ZERO_POS == cmd) ||
                     (CMD_READ_POS == cmd));
}

/**
 * @输入 cmd: 命令码
 * @输出 1=受校准门控约束（未校准即回 0xE1）
 * @说明 0x09 读位置**豁免**：只读诊断不改运动状态，急停后置未校准仍可取证
 *       （依据 require.md 附录A v2.1 门控说明）
 */
static uint8_t S_CmdCalibGated(uint8_t cmd)
{
    return (uint8_t)((0U != S_CmdUsesMotor(cmd)) && (CMD_READ_POS != cmd));
}

/**
 * @输入 cmd: 已解析命令
 * @输出 1=急停（0x07 且 Can[6]=0xFF），0=其他
 */
static uint8_t S_IsEStop(const CAN_CMD_T *cmd)
{
    return (uint8_t)((CMD_STOP_ESTOP == cmd->cmd) &&
                     (0xFFU == (uint8_t)(cmd->value & 0xFF)));
}

/**
 * @输入 cmd: 已解析命令; sign: +1/-1/0（相对方向，0=停止）
 * @输出 无
 * @说明 相对/停止命令的到位跟踪目标预计算（任务A直读 XACTUAL，新鲜）
 */
static void S_ArmFromNow(const CAN_CMD_T *cmd, int32_t sign)
{
    TMC5160_CHIP_T *c1 = MOTOR_GetChip(MOTOR_CTRL_U1);
    TMC5160_CHIP_T *c2 = MOTOR_GetChip(MOTOR_CTRL_U2);
    int32_t t1 = 0;
    int32_t t2 = 0;

    if ((void *)0 != c1)
    {
        t1 = TMC5160_GetPosition(c1);
    }
    if ((void *)0 != c2)
    {
        t2 = TMC5160_GetPosition(c2);
    }
    if (0 == sign)
    {
        /* 停止：以当前位置为目标 */
        SENSE_ArmCompletion(cmd->motor, cmd->cmd, cmd->param, t1, t2);
    }
    else
    {
        SENSE_ArmCompletion(cmd->motor, cmd->cmd, cmd->param,
                            t1 + sign * cmd->value,
                            t2 + sign * cmd->value);
    }
}

/* ==== 接口实现 ==== */

/**
 * @输入 无
 * @输出 无
 * @说明 从队列取一条命令（ISR 已校验）解析执行并发反馈
 *        仅任务A调用（SPI + CAN TX 合法）
 */
void CAN_CMD_Process(void)
{
    uint8_t *cmd_data;
    CAN_CMD_T cmd;

    if (QUEUE_IsEmpty(&g_queue_st))
    {
        return;
    }

    cmd_data = (uint8_t *)QUEUE_First(&g_queue_st);
    if (QUEUE_NULL_PTR == cmd_data)
    {
        return;
    }

    /* v2 解析：Can[0]=cmd, Can[1]=motor, Can[2]=param, Can[3..6]=value 大端 */
    cmd.cmd      = cmd_data[0];
    cmd.motor    = cmd_data[1];
    cmd.param    = cmd_data[2];
    cmd.value    = S_BytesToInt32BE(&cmd_data[3]);
    cmd.checksum = cmd_data[7];
    QUEUE_Delete(&g_queue_st);

    /* ==== 急停特判（最高优先，无视电机掩码） ====
     * 急停语义=意外时的强制关断（require.md 附录A v2.1，用户 2026-09-30 定：
     * 关闭所有电机，不管 Can[1] 电机号是 0x00/越界/任意值）；必须在掩码校验
     * 之前拦截，否则非法掩码会走 0xE3 拒收、根本不关功率级 */
    if (0U != S_IsEStop(&cmd))
    {
        SENSE_Disarm(0xFFU);
        HOME_MarkUncalibrated(0xFFU);
        MOTOR_EStop(0xFFU);
        (void)CAN_SendErrorRaw(CAN_ERR_UNCALIBRATED);
        return;
    }

    /* ==== 掩码校验（所有带 motor 的命令） ==== */
    if (0U != S_CmdUsesMotor(cmd.cmd))
    {
        if (0U == MOTOR_MASK_VALID(cmd.motor))
        {
            (void)CAN_SendErrorRaw(CAN_ERR_INVALID_MASK);
            return;
        }
    }

    /* ==== 校准门控（0x09 读位置豁免：只读不改运动状态） ====
     * 急停已在上方全局拦截，此处不再特判 */
    if (0U != S_CmdCalibGated(cmd.cmd))
    {
        /* 正在校准中：HOME 忙拒（不打断）；其他命令打断回零→未校准并回忙 */
        if (0U != HOME_IsCalibrating(cmd.motor))
        {
            if (CMD_HOME == cmd.cmd)
            {
                (void)CAN_SendErrorRaw(CAN_ERR_CALIBRATING);
                return;
            }
            HOME_Abort(cmd.motor);
            (void)CAN_SendErrorRaw(CAN_ERR_CALIBRATING);
            return;
        }
        /* 未校准：仅 HOME 允许（急停已在全局层拦截） */
        if ((0U != HOME_IsUncalibrated(cmd.motor)) && (CMD_HOME != cmd.cmd))
        {
            (void)CAN_SendErrorRaw(CAN_ERR_UNCALIBRATED);
            return;
        }
    }

    /* 命令分发（就地执行） */
    switch (cmd.cmd)
    {
    case CMD_ABS_POS:
        MOTOR_ApplyProfile(cmd.motor, cmd.param);
        MOTOR_MoveTo(cmd.motor, cmd.value);
        SENSE_ArmCompletion(cmd.motor, cmd.cmd, cmd.param,
                            cmd.value, cmd.value);
        (void)CAN_SendAckRaw(cmd.cmd);
        break;

    case CMD_REL_CW:
        MOTOR_ApplyProfile(cmd.motor, cmd.param);
        S_ArmFromNow(&cmd, 1);          /* 先读位置算目标，再执行偏移 */
        MOTOR_MoveBy(cmd.motor, cmd.value);
        (void)CAN_SendAckRaw(cmd.cmd);
        break;

    case CMD_REL_CCW:
        MOTOR_ApplyProfile(cmd.motor, cmd.param);
        S_ArmFromNow(&cmd, -1);
        MOTOR_MoveBy(cmd.motor, -cmd.value);
        (void)CAN_SendAckRaw(cmd.cmd);
        break;

    case CMD_VELOCITY:
    {
        /* Can[6]=0x00正转/0xFF反转；速度取参数组档位 VMAX（无终态） */
        uint8_t neg = (0xFFU == (uint8_t)(cmd.value & 0xFF)) ? 1U : 0U;
        uint32_t spd = TMC5160_GetProfileVmax(cmd.param);

        MOTOR_ApplyProfile(cmd.motor, cmd.param);
        MOTOR_SetVelocity(cmd.motor,
                          (0U != neg) ? -(int32_t)spd : (int32_t)spd);
        (void)CAN_SendAckRaw(cmd.cmd);
        break;
    }

    case CMD_HOME:
    {
        /* 回零：Can[6]=0x00负向/0xFF正向寻零；掩码并发；完成由 Tick 发到位 */
        uint8_t dir = (0xFFU == (uint8_t)(cmd.value & 0xFF)) ?
                      TMC5160_HOME_DIR_POSITIVE : TMC5160_HOME_DIR_NEGATIVE;

        SENSE_Disarm(cmd.motor);
        if (0U == HOME_Start(cmd.motor, dir, cmd.param))
        {
            (void)CAN_SendAckRaw(cmd.cmd);
        }
        break;
    }

    case CMD_CL_SET:
    {
        /* 闭环使能/失能：Can[6]=0x00使能/0xFF失能；即时完成逐电机各一帧 */
        uint8_t en = (0xFFU == (uint8_t)(cmd.value & 0xFF)) ? 0U : 1U;
        uint8_t i;

        for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
        {
            TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

            if (((void *)0 != chip) &&
                (0U != (cmd.motor & MOTOR_MASK_BIT(chip->id))))
            {
                if (0U != en)
                {
                    CLOSEDLOOP_Enable(chip->id);
                }
                else
                {
                    CLOSEDLOOP_Disable(chip->id);
                }
            }
        }
        (void)CAN_SendAckRaw(cmd.cmd);
        for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
        {
            TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);
            uint8_t status;

            if (((void *)0 == chip) ||
                (0U == (cmd.motor & MOTOR_MASK_BIT(chip->id))))
            {
                continue;
            }
            /* 即时完成帧：状态位非破坏直读 + 到位强制 1（本命令即时完成） */
            status = TMC5160_ReadStatusFlags(chip);
            status = (uint8_t)((status & (uint8_t)(~CAN_ST_ARRIVED)) |
                               CAN_ST_ARRIVED);
            (void)CAN_SendArrivalRaw(cmd.cmd, chip->id, cmd.param, status);
        }
        break;
    }

    case CMD_READ_POS:
    {
        /* 读位置（v2.1，诊断只读，校准门控豁免）：先 ACK，掩码内每电机一帧
         * 位置反馈；只做 1 次 SPI 读（该次读的 0xFFFFFFFF 哨兵即 SPI 异常位）；
         * 位置源由 Can[2] 选：0=XACTUAL(指令，默认)/1=X_ENC(编码器)；直读而非
         * 快照——快照 2ms 滞后在 G6 610rpm ≈1041µsteps，与 encoder_tol=512 同
         * 量级，会引入假偏差 */
        uint8_t i;
        uint8_t src = (TMC5160_POS_SRC_X_ENC == cmd.param) ?
                      TMC5160_POS_SRC_X_ENC : TMC5160_POS_SRC_XACTUAL;

        (void)CAN_SendAckRaw(cmd.cmd);
        for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
        {
            TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);
            int32_t pos = 0;
            uint8_t status = 0U;

            if (((void *)0 == chip) ||
                (0U == (cmd.motor & MOTOR_MASK_BIT(chip->id))))
            {
                continue;
            }
            if (0U != TMC5160_ReadPosition(chip, src, &pos))
            {
                status = CAN_ST_SPIFAIL;
            }
            (void)CAN_SendPosRaw(cmd.cmd, chip->id, status, pos);
        }
        break;
    }

    case CMD_STOP_ESTOP:
        /* 急停（value=0xFF）已在函数入口全局拦截；此处仅一般停止（value=0x00） */
        S_ArmFromNow(&cmd, 0);              /* 停止即以当前位置为目标 */
        MOTOR_Stop(cmd.motor);
        (void)CAN_SendAckRaw(cmd.cmd);
        break;

    case CMD_ZERO_POS:
        /* 在线清零：停稳→XACTUAL/X_ENC 写 0→offset 归零→Arm 0 目标；
         * 仅已校准电机可达此（门控在上） */
        SENSE_Disarm(cmd.motor);
        MOTOR_Stop(cmd.motor);
        S_ZeroCounters(cmd.motor);
        SENSE_ArmCompletion(cmd.motor, cmd.cmd, cmd.param, 0, 0);
        (void)CAN_SendAckRaw(cmd.cmd);
        break;

    default:
        (void)CAN_SendErrorRaw(CAN_ERR_INVALID_CMD);
        break;
    }
}
