/*****************************************************************************
 * @文件: sense.c
 * @作者: cl
 * @日期: 2026-09-22
 * @版本: v1.0
 * @说明: 到位跟踪与终态 mailbox 实现（摆法2）
 *        CheckCompletion 纯读 sched 快照（任务B，无 SPI）；
 *        终态 status/protect 由任务A取走时实时 SPI 补齐后发送
 *        跨任务纪律（单核 + volatile 程序序）：写方最后写 active=1；
 *        读方先读 active 再读负载；读方清 active=0
 * @依赖: app/sched(快照), drv/tmc5160(芯片表), app/motor_ctrl(编号),
 *        drv/can(STATUS_* 位宏), main.h(HAL_GetTick)
 ****************************************************************************/
#include "app/sense.h"
#include "app/sched.h"
#include "drv/tmc5160.h"
#include "app/motor_ctrl.h"
#include "hal/can.h"
#include "main.h"

/* 到位终态跟踪：ACK 只表示收到，bit0 强制清零；目标到达后补发终态帧
 * 超时推导: 最慢参数组 VMAX=5000 → 整圈 51200/5000≈10.2s + 加减速，
 * 取 30s(约×3 裕量，待实测确认)（自 can.c 迁入，值未变） */
#define SENSE_COMPLETION_TIMEOUT_MS  30000U

typedef struct {
    volatile uint8_t active;
    volatile uint8_t motor;
    volatile uint8_t cmd;
    volatile uint8_t param;
    volatile int32_t target;
    volatile uint32_t deadline;
} SENSE_PENDING_T;

static SENSE_DONE_T s_done[TMC5160_CHIP_COUNT];
static SENSE_PENDING_T s_pending[TMC5160_CHIP_COUNT];

/* ==== 内部工具 ==== */

static void S_PostDone(uint8_t motor, uint8_t cmd, uint8_t param, uint8_t arrived)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        if ((0U == s_done[i].active) || (s_done[i].motor == motor))
        {
            s_done[i].motor = motor;
            s_done[i].cmd = cmd;
            s_done[i].param = param;
            s_done[i].arrived = arrived;
            s_done[i].active = 1U;
            return;
        }
    }
    /* 槽满：覆盖最旧同电机槽位无意义，静默丢（仅2电机+人类速率不应命中） */
}

/* ==== 到位跟踪 ==== */

/**
 * @输入 motor: 电机掩码(bit(i)=电机i+1); cmd/param: 回显命令码/参数组;
 *        t1/t2: 本次指令目标绝对位置(U1/U2)
 * @输出 无
 * @说明 登记到位跟踪（掩码内各电机各一槽）；ACK 由调用方发（命令码回显）
 */
void SENSE_ArmCompletion(uint8_t motor, uint8_t cmd, uint8_t param,
                         int32_t t1, int32_t t2)
{
    uint32_t now = HAL_GetTick();

    if (0U != (motor & MOTOR_MASK_BIT(MOTOR_CTRL_U1)))
    {
        s_pending[0].active = 1;
        s_pending[0].motor = MOTOR_CTRL_U1;
        s_pending[0].cmd = cmd;
        s_pending[0].param = param;
        s_pending[0].target = t1;
        s_pending[0].deadline = now + SENSE_COMPLETION_TIMEOUT_MS;
    }
    if (0U != (motor & MOTOR_MASK_BIT(MOTOR_CTRL_U2)))
    {
        s_pending[1].active = 1;
        s_pending[1].motor = MOTOR_CTRL_U2;
        s_pending[1].cmd = cmd;
        s_pending[1].param = param;
        s_pending[1].target = t2;
        s_pending[1].deadline = now + SENSE_COMPLETION_TIMEOUT_MS;
    }
}

/**
 * @输入 motor: 电机掩码(bit(i)=电机i+1)
 * @输出 无
 * @说明 取消掩码内电机的到位跟踪（回零/急停接管运动后旧目标作废）
 */
void SENSE_Disarm(uint8_t motor)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        if ((0U != s_pending[i].active) &&
            (0U != (motor & MOTOR_MASK_BIT(s_pending[i].motor))))
        {
            s_pending[i].active = 0;
        }
    }
}

/**
 * @输入 无
 * @输出 无
 * @说明 每圈调用（任务B，纯读快照无 SPI）：XACTUAL==目标且 VACTUAL 归零
 *        → 投递到位到 done 槽（cmd/param/arrived，任务A取走时实时补过温/
 *        驱动异常后发送到位反馈）；超时也投一帧（arrived=0）
 *        快照新鲜度=任务A快采周期（2ms），到位判定可接受
 */
void SENSE_CheckCompletion(void)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        if (0U == s_pending[i].active)
        {
            continue;
        }
        {
            const SCHED_SNAP_T *snap = SCHED_Snap(s_pending[i].motor);
            uint32_t now = HAL_GetTick();
            uint8_t arrived;
            uint8_t expired;

            if ((void *)0 == snap)
            {
                s_pending[i].active = 0;
                continue;
            }
            /* 到达：位置吻合且速度归零；超时：回绕安全的差值判 */
            arrived = (snap->x_actual == s_pending[i].target &&
                       0 == snap->vactual);
            expired = (0 <= (int32_t)(now - s_pending[i].deadline));
            if ((0U != arrived) || (0U != expired))
            {
                s_pending[i].active = 0;
                /* 到位=1，超时=0；任务A取走时实时补过温/驱动异常后发到位反馈 */
                S_PostDone(s_pending[i].motor, s_pending[i].cmd,
                           s_pending[i].param, (0U != arrived) ? 1U : 0U);
            }
        }
    }
}

uint8_t SENSE_TakeDone(uint8_t motor, SENSE_DONE_T *out)
{
    uint8_t i;

    if ((void *)0 == out)
    {
        return 0U;
    }
    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        if ((0U != s_done[i].active) && (s_done[i].motor == motor))
        {
            out->active = 1U;
            out->motor = s_done[i].motor;
            out->cmd = s_done[i].cmd;
            out->param = s_done[i].param;
            out->arrived = s_done[i].arrived;
            s_done[i].active = 0U;
            return 1U;
        }
    }
    return 0U;
}
