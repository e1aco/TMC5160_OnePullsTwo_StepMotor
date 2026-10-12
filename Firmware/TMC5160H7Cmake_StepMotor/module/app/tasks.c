/*****************************************************************************
 * @文件: tasks.c
 * @作者: cl
 * @日期: 2026-09-22
 * @版本: v1.0
 * @说明: RTOS 任务层实现（任务创建 + 任务体）
 * @依据: RTOS 移植裁决 2026-09-22 + 2026-09-23 摆法2：
 *        任务A = 收拆CAN分发+就地SPI执行+采快照+闭环同拍算写+回零+发帧
 *        （ACK/终态统一归任务A，CAN TX 唯一写者）
 *        任务B = 仅到位判定（纯读快照，不碰SPI不发CAN）
 * @依赖: cmsis_os2, app/sched, app/can_cmd, app/sense, app/motor_ctrl,
 *        app/homing, app/closed_loop, drv/can, drv/tmc5160, drv/wdg,
 *        drv/uart_dbg, drv/rtt_dbg, test/comm_test, test/tim_test
 ****************************************************************************/
#include "app/tasks.h"
#include "cmsis_os2.h"
#include "main.h"

#include "app/sched.h"
#include "app/can_cmd.h"
#include "app/sense.h"
#include "app/motor_ctrl.h"
#include "app/homing.h"
#include "app/closed_loop.h"
#include "hal/can.h"
#include "drv/tmc5160.h"
#include "hal/wdg.h"
#include "hal/uart_dbg.h"
#include "hal/rtt_dbg.h"
#include "comm_test.h"
#include "tim_test.h"

/* ==== 任务周期常量 ==== */
#define TASK_SENSE_PERIOD_MS    2U      /* 任务A 感知节拍（2ms 全量） */
#define TASK_CTRL_PERIOD_MS     1U      /* 任务B 到位判定节拍 */
#define TASK_TMR_PERIOD_MS      1000U   /* 遥测回传 1Hz */
#define TASK_WDG_PERIOD_MS      200U    /* 喂狗间隔（IWDG 超时标称 5s） */
#define TASK_DIAG_PERIOD_MS     1000U   /* 诊断采集 1Hz（供遥测/ACK 快照） */

/* ==== 任务属性（stack_size 单位=字节，CMSIS-RTOS2 内部 ÷4 转 word；见 cmsis_os2.c） ==== */
static const osThreadAttr_t s_sense_attr = {
    .name = "senseTask",                /* 任务A */
    .stack_size = 2048U,
    .priority = (osPriority_t)osPriorityHigh,   /* 40：最高，独占实时槽+SPI */
};
static const osThreadAttr_t s_ctrl_attr = {
    .name = "ctrlTask",                 /* 任务B：仅快照到位判定 */
    .stack_size = 1024U,
    .priority = (osPriority_t)28,   /* 任务A 40 > 任务B 28 > tmr 16 > wdg 8 */
};
static const osThreadAttr_t s_tmr_attr = {
    .name = "tmrTask",
    .stack_size = 1024U,
    .priority = (osPriority_t)osPriorityBelowNormal,   /* 16（用户 2026-09-22 再降） */
};
static const osThreadAttr_t s_wdg_attr = {
    .name = "wdgTask",
    .stack_size = 512U,
    .priority = (osPriority_t)osPriorityLow,           /* 8：最低=高优饿死即停喂，复位真实 */
};

static osThreadId_t s_sense_tid;
static osThreadId_t s_ctrl_tid;
static osThreadId_t s_tmr_tid;
static osThreadId_t s_wdg_tid;

/* ==== 任务体 ==== */

/**
 * @说明 任务A（原 senseTask）：SPI 唯一 owner，2ms 节拍，优先级 High(40)
 *       职责：CAN 分发(就地执行+快照ACK) → 快照采集 → 闭环 Tick 同拍算写
 *       → 回零状态机 → 取终态(实时SPI补齐 status/protect)发帧 → 心跳/诊断
 *       CAN TX 唯一写者（ACK + 终态 + 回零反馈统一在本任务）
 *       2ms 节奏下 CAN 命令响应最坏 ~2ms（摆法2 已接受代价）
 */
static void S_SenseTask(void *argument)
{
    (void)argument;

    for (;;)
    {
#ifdef CL_TIMING_MEASURE
        static uint32_t s_tm2_cnt = 0U;
        uint8_t tm2_on = 0U;

        if ((++s_tm2_cnt % 500U) == 0U)   /* 抽稀 500:1，避 RTT 回传反压 */
        {
            tm2_on = 1U;
            TEST_TIM_Start(2);            /* tag2=任务A全圈 */
        }
#endif
        /* 1) CAN 分发：解析一次→就地执行→快照 ACK（无服务 FIFO） */
        CAN_CMD_Process();

        /* 2) SPI 采集快照（含 vactual，供任务B到位判定） */
        SCHED_CollectFast();

        /* 3) 闭环 Tick：同任务直接落盘（零滞后），2ms 节奏待 T1-like 重验 */
        TEST_TIM_Start(7);   /* tag7=双电机Tick段耗时 */
        CLOSEDLOOP_Tick(MOTOR_CTRL_U1);
        CLOSEDLOOP_Tick(MOTOR_CTRL_U2);
        TEST_TIM_Stop(7);

        /* 4) 回零状态机（含 SPI + 终态 CAN，TX 归本任务） */
        HOME_Tick();

        /* 5) 到位 mailbox：任务B已写槽，本任务取走并实时补过温/驱动异常
         *    后发到位反馈（GSTAT drv_err 粘滞位见即清，防常 1 污染后续帧） */
        {
            uint8_t i;
            SENSE_DONE_T done;

            for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
            {
                TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

                if (((void *)0 != chip) &&
                    (0U != SENSE_TakeDone(chip->id, &done)))
                {
                    /* 状态位非破坏直读（到位/失步/过温/驱动错/SPI异常），
                     * bit0 用软件到位判定覆盖（超时=0）；drv_err 粘滞位见即清，
                     * 防常 1 污染后续帧 */
                    uint8_t status = TMC5160_ReadStatusFlags(chip);

                    status = (uint8_t)((status & (uint8_t)(~CAN_ST_ARRIVED)) |
                                       ((0U != done.arrived) ?
                                        CAN_ST_ARRIVED : 0U));
                    if (0U != (status & CAN_ST_DRVERR))
                    {
                        (void)TMC5160_SpiWrite(chip, REG_GSTAT, 0x02U);
                    }
                    (void)CAN_SendArrivalRaw(done.cmd, done.motor, done.param,
                                             status);
                }
            }
        }

        COMM_Test_CAN_Heartbeat();        /* CAN RX 计数回显 */

        /* 1Hz 诊断采集（RAMP_STAT/DRVSTATUS/GSTAT/ENC_STATUS）供遥测+ACK 快照 */
        {
            static uint32_t s_diag_last = 0U;
            uint32_t now = HAL_GetTick();

            if ((now - s_diag_last) >= TASK_DIAG_PERIOD_MS)
            {
                s_diag_last = now;
                TEST_TIM_Start(5);        /* tag5=1Hz 诊断 SPI 块 */
                SCHED_CollectDiag();
                TEST_TIM_Stop(5);
            }
        }

#ifdef CL_TIMING_MEASURE
        /* 3-wire Saleae 连续触发: 每 10ms 一次 U2 GSTAT 读, PA4 窄脉冲作同步 */
        {
            static uint32_t s_saleae_last = 0U;
            uint32_t now = HAL_GetTick();

            if ((now - s_saleae_last) >= 10U)
            {
                s_saleae_last = now;
                TEST_SPI_SaleaePulse();
                (void)TMC5160_GetGStat(TMC5160_ChipAt(1U));
            }
        }
#endif
#ifdef CL_TIMING_MEASURE
        if (0U != tm2_on)
        {
            TEST_TIM_Stop(2);
        }
#endif
        /* 偏差遥测 20Hz（PID 整定用开环偏差-时间曲线；RTT 通道 0） */
        {
            static uint32_t s_cld_last = 0U;
            uint32_t now = HAL_GetTick();

            if ((now - s_cld_last) >= 50U)
            {
                s_cld_last = now;
                RTT_DBG_Printf("[CLD] t=%u d1=%d d2=%d\r\n",
                               (unsigned)now,
                               (int)CLOSEDLOOP_GetDeviation(MOTOR_CTRL_U1),
                               (int)CLOSEDLOOP_GetDeviation(MOTOR_CTRL_U2));
            }
        }
        osDelay(TASK_SENSE_PERIOD_MS);
    }
}

/**
 * @说明 任务B（原 ctrlTask）：仅到位判定，纯读 sched 快照
 *       不碰 SPI、不发 CAN；命中投 done 槽由任务A取走发送
 *       优先级 28（任务A 40 > 任务B 28），1ms osDelay
 */
static void S_CtrlTask(void *argument)
{
    (void)argument;

    for (;;)
    {
        SENSE_CheckCompletion();    /* 快照到位判定（无 SPI） */
        osDelay(TASK_CTRL_PERIOD_MS);
    }
}

/**
 * @说明 遥测任务：1Hz 双通道回传（USART1 + RTT），数据取自快照，不触碰 SPI
 *       优先级 BelowNormal(16)：回传阻塞不抢占感知与控制
 *       待实测：RTT 1KB 缓冲 printf 耗时是否 >1ms（阻塞期间闭环停摆）
 */
static void S_TmrTask(void *argument)
{
    (void)argument;

    for (;;)
    {
        const SCHED_SNAP_T *u2 = SCHED_Snap(MOTOR_CTRL_U2);
        uint32_t now = HAL_GetTick();

        TEST_TIM_Start(6);   /* tag6=tmr回传阻塞窗（T3判据<1ms期望,超限只记tmr抖动） */
        if ((void *)0 != u2)
        {
            UART_DBG_Printf("[t=%u] U1 act=%d enc=%d | U2 act=%d enc=%d "
                            "v=%d rs=%lX cs=%lu ds=%08lX gs=%02X\r\n",
                            (unsigned)now,
                            (int)SCHED_GetActual(MOTOR_CTRL_U1),
                            (int)SCHED_GetEncoder(MOTOR_CTRL_U1),
                            (int)SCHED_GetActual(MOTOR_CTRL_U2),
                            (int)SCHED_GetEncoder(MOTOR_CTRL_U2),
                            (int)u2->vactual,
                            (unsigned long)u2->ramp_stat,
                            (unsigned long)(u2->drv_status & 0x3FFUL),
                            (unsigned long)u2->drv_status,
                            (unsigned int)u2->gstat);
        }
        /* RTT 1Hz 遥测（含失步/故障位，长时 soak 观察）：
         * ds=DRVSTATUS gs=GSTAT es=ENC_STATUS（bit1=deviation_warn 失步） */
        RTT_DBG_Printf("[t=%u] U1 act=%d enc=%d | U2 act=%d enc=%d "
                       "ds=%08lX gs=%02X es=%02X\r\n",
                       (unsigned)now,
                       (int)SCHED_GetActual(MOTOR_CTRL_U1),
                       (int)SCHED_GetEncoder(MOTOR_CTRL_U1),
                       (int)SCHED_GetActual(MOTOR_CTRL_U2),
                       (int)SCHED_GetEncoder(MOTOR_CTRL_U2),
                       (unsigned long)((void *)0 != u2 ? u2->drv_status : 0U),
                       (unsigned int)((void *)0 != u2 ? u2->gstat : 0U),
                       (unsigned int)((void *)0 != u2 ? u2->enc_status : 0U));
        /* 栈水位1Hz上报（字节余量，CMSIS层osThreadGetStackSpace→FreeRTOS watermark；
         * 依据 .cl/memory/ rtos_heap=15360B + 静态估算ctrl2048/cl1024/tmr1024/wdg512） */
        RTT_DBG_Printf("[STK] sense=%u ctrl=%u tmr=%u wdg=%u free\r\n",
                       (unsigned)osThreadGetStackSpace(s_sense_tid),
                       (unsigned)osThreadGetStackSpace(s_ctrl_tid),
                       (unsigned)osThreadGetStackSpace(s_tmr_tid),
                       (unsigned)osThreadGetStackSpace(s_wdg_tid));
        TEST_TIM_Stop(6);

        osDelay(TASK_TMR_PERIOD_MS);
    }
}

/**
 * @说明 看门狗任务：周期喂狗。最低优先级 → 任一更高优任务饿死 CPU 时本任务
 *       得不到调度，IWDG 5s 到期复位，失效安全（原风险日志"无 IWDG"行的正解）
 *       2026-09-22：恒喂（不再以 WDG_IsOn 门控）——MX_IWDG1_Init 必启动 IWDG，
 *       门控会在同步告警时停喂致 5s 复位环；WDG_IsOn 仅诊断用
 */
static void S_WdgTask(void *argument)
{
    (void)argument;

    for (;;)
    {
#if WDG_ENABLE
        WDG_Feed();
#endif
        osDelay(TASK_WDG_PERIOD_MS);
    }
}

/* ==== 创建 ==== */
void TASKS_Init(void)
{
    s_sense_tid = osThreadNew(S_SenseTask, (void *)0, &s_sense_attr);
    s_ctrl_tid = osThreadNew(S_CtrlTask, (void *)0, &s_ctrl_attr);
    s_tmr_tid  = osThreadNew(S_TmrTask, (void *)0, &s_tmr_attr);
    s_wdg_tid  = osThreadNew(S_WdgTask, (void *)0, &s_wdg_attr);
}
