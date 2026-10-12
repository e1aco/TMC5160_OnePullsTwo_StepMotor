/*****************************************************************************
 * @文件: aging_test.c
 * @作者: cl
 * @日期: 2026-09-30
 * @版本: v1.0
 * @说明: 内建老化/整体自测（pre-RTOS 阻塞运行，见 module/config.h CL_AGING_TEST）
 *        按 CL_AG_TEST_MASK 位选：SELFTEST 基础通路 / AGING 多档老化 /
 *        THERMAL 温升位 / ESTOP 急停 / LED 指示；全部结果经 RTT 打印。
 *        运行期无 RTOS（直调 drv/app），IWDG 已在 MX_IWDG1_Init 启动 → 循环内喂狗。
 * @依赖: main.h(HAL/时钟), drv/tmc5160, app/motor_ctrl, hal/rtt_dbg, hal/wdg
 ****************************************************************************/
#include "aging_test.h"

#if CL_AGING_TEST

#include "main.h"
#include "drv/tmc5160.h"
#include "app/motor_ctrl.h"
#include "hal/rtt_dbg.h"
#include "hal/wdg.h"
#include "hal/nvm.h"

/* ==== 常量 ==== */
#define AG_BEAT_MS          60000U   /* 老化心跳打印周期 60s（正式） */
#define AG_LEG_TIMEOUT_MS   30000U   /* 单腿到位超时（G1 1圈≈11s，留裕量） */
/* 状态位故障掩码 bit1..4：失步/过温/驱动错/SPI异常（同 TMC5160_ReadStatusFlags） */
#define AG_FAULT_MASK       0x1EU

/* ==== 调试状态（J-Link read-mem 直读；RTT 实时流不可靠时观测用） ==== */
typedef struct {
    uint32_t phase;    /* 0 idle/1 selftest/2 aging/3 estop/4 led/5 done */
    uint32_t prof;
    uint32_t legs;
    int32_t  maxdev;
    uint32_t now;
    uint32_t tend;
    uint32_t boot;
    uint32_t st_spi;   /* selftest: SPI 1=ok */
    uint32_t st_move;  /* selftest: move 1=ok */
    int32_t  dev1;     /* selftest: U1 dev */
    int32_t  dev2;     /* selftest: U2 dev */
    int32_t  mtgt;     /* 移动: U1 目标 */
    int32_t  mlast;    /* 移动: U1 末次实测位置 */
    uint32_t st_ok;    /* selftest 总结果 1=PASS */
    uint32_t st_flt1;  /* selftest: U1 故障位 */
    uint32_t ot_seen;  /* 运行中过温标志累计（bit2=ot/otpw） */
    uint32_t pl_legs[TMC5160_PROFILE_COUNT];    /* 各档 leg 数 */
    int32_t  pl_maxdev[TMC5160_PROFILE_COUNT];  /* 各档 maxdev */
} ag_status_t;

volatile ag_status_t g_ag_status;

/* ==== 内部工具 ==== */

/**
 * @输入 chip: 芯片; spi_ok/fault: 输出（SPI 有效 / 故障位）
 * @输出 int32_t: dev = XACTUAL − X_ENC（µsteps；两者同量纲，实测 Δ比≈0.999）
 * @说明 读指令位置与编码器位置算偏差，并取非破坏状态位（含失步位）
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md: XACTUAL
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p045.md: X_ENC (R 0x39)
 */
static int32_t S_ReadDev(TMC5160_CHIP_T *chip, uint8_t *spi_ok, uint8_t *fault)
{
    int32_t xa = 0;
    int32_t xe = 0;
    uint8_t e1;
    uint8_t e2;
    uint8_t st;

    e1 = TMC5160_ReadPosition(chip, TMC5160_POS_SRC_XACTUAL, &xa);
    e2 = TMC5160_ReadPosition(chip, TMC5160_POS_SRC_X_ENC, &xe);
    st = TMC5160_ReadStatusFlags(chip);
    *spi_ok = (uint8_t)(((0U == e1) && (0U == e2) &&
                         (0U == (st & 0x10U))) ? 1U : 0U);
    *fault = (uint8_t)(st & AG_FAULT_MASK);
    return xa - xe;
}

/* RAMP_STAT.position_reached=bit9（R） 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md */
#define AG_RAMP_POS_REACHED  (1UL << 9)

/**
 * @输入 mask: 电机位掩码; offset: 相对位移 µsteps; timeout_ms: 到位超时
 * @输出 1=所有选中电机到位, 0=超时
 * @说明 发 MoveBy → 轮询各电机 RAMP_STAT.position_reached(bit9) 判到位；
 *        轮询内 WDG_Feed 喂狗。不用 XACTUAL==目标 精确等值（实测终端不精确相等）
 */
static uint8_t S_MoveAndWait(uint8_t mask, int32_t offset, uint32_t timeout_ms)
{
    uint32_t t0;
    uint8_t i;

    MOTOR_MoveBy(mask, offset);
    t0 = HAL_GetTick();
    while ((uint32_t)(HAL_GetTick() - t0) < timeout_ms)
    {
        uint8_t all = 1U;

        WDG_Feed();
        for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
        {
            TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

            if (((void *)0 != chip) &&
                (0U != (mask & MOTOR_MASK_BIT(chip->id))))
            {
                if (0U == (TMC5160_GetRampStat(chip) & AG_RAMP_POS_REACHED))
                {
                    all = 0U;
                }
            }
        }
        if (0U != all)
        {
            g_ag_status.mlast = TMC5160_GetPosition(TMC5160_ChipAt(0));
            return 1U;
        }
        HAL_Delay(2U);
    }
    g_ag_status.mlast = TMC5160_GetPosition(TMC5160_ChipAt(0));
    return 0U;
}

/* ==== 子测试 ==== */

/**
 * @输出 1=PASS, 0=FAIL
 * @说明 基础通路：时钟、逐芯片 SPI 通信（GCONF/CHOPCONF 哨兵）、双机 1 圈到位、
 *        编码器一致性 |XACTUAL-X_ENC|<=CL_AG_TOL
 */
static uint8_t S_SelfTest(void)
{
    uint8_t ok = 1U;
    uint8_t i;

    RTT_DBG_Printf("[SELFTEST] HCLK=%lu Hz\r\n",
                   (unsigned long)HAL_RCC_GetHCLKFreq());
    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);
        uint32_t gconf;
        uint32_t chop;

        if ((void *)0 == chip)
        {
            continue;
        }
        gconf = TMC5160_SpiRead(chip, REG_GCONF);
        chop = TMC5160_SpiRead(chip, REG_CHOPCONF);
        RTT_DBG_Printf("[SELFTEST] U%u GCONF=%08lX CHOPCONF=%08lX %s\r\n",
                       chip->id, (unsigned long)gconf, (unsigned long)chop,
                       ((0xFFFFFFFFUL == gconf) || (0xFFFFFFFFUL == chop)) ?
                       "SPI_FAIL" : "SPI_OK");
        if ((0xFFFFFFFFUL == gconf) || (0xFFFFFFFFUL == chop))
        {
            ok = 0U;
        }
    }
    MOTOR_ApplyProfile(MOTOR_CTRL_ALL, 1U);   /* 慢档，安静 */
    g_ag_status.st_spi = ok;   /* 仅 SPI 通道作硬门控（决定是否进老化） */
    if (0U == S_MoveAndWait(MOTOR_CTRL_ALL, (int32_t)CL_AG_TRAVEL, 20000U))
    {
        RTT_DBG_Str("[SELFTEST] move timeout (soft)\r\n");
        g_ag_status.st_move = 0U;
        ok = 0U;
    }
    else
    {
        g_ag_status.st_move = 1U;
    }
    MOTOR_Stop(MOTOR_CTRL_ALL);
    HAL_Delay(200U);
    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);
        int32_t dev;
        uint8_t sp = 1U;
        uint8_t flt = 0U;

        if ((void *)0 == chip)
        {
            continue;
        }
        dev = S_ReadDev(chip, &sp, &flt);
        RTT_DBG_Printf("[SELFTEST] U%u dev=%ld spi=%u flt=%02X %s\r\n",
                       chip->id, (long)dev, (unsigned)sp, (unsigned)flt,
                       ((0U != sp) && (0U == flt) &&
                        (dev <= (int32_t)CL_AG_TOL) &&
                        (dev >= -(int32_t)CL_AG_TOL)) ? "OK" : "FAIL");
        if (1U == chip->id)
        {
            g_ag_status.dev1 = dev;
            g_ag_status.st_flt1 = flt;
        }
        else
        {
            g_ag_status.dev2 = dev;
        }
        if ((0U == sp) || (0U != flt) ||
            (dev > (int32_t)CL_AG_TOL) || (dev < -(int32_t)CL_AG_TOL))
        {
            ok = 0U;   /* 软失败：仅记录，不门控老化 */
        }
    }
    RTT_DBG_Printf("[SELFTEST] RESULT %s\r\n", (0U != ok) ? "PASS" : "FAIL");
    g_ag_status.st_ok = ok;
    return g_ag_status.st_spi;   /* 只有 SPI 通不过才阻止运动老化 */
}

/**
 * @输入 mask: CL_AG_TEST_MASK（bit2 THERMAL 决定 beat 是否附 OT/OTPW）
 * @输出 1=PASS, 0=FAIL
 * @说明 多档老化：依 CL_AG_PROFILES 遍历 G1~G6，每档跑 CL_AG_SEG_MIN 分钟；
 *        每腿正/反 1 圈到位后读 dev + 故障位；dev>tol 或故障即停并报。
 */
static uint8_t S_Aging(uint8_t mask)
{
    uint8_t ok = 1U;
    uint8_t p;

    for (p = 1U; p <= TMC5160_PROFILE_COUNT; p++)
    {
        uint32_t t_end;
        uint32_t legs = 0U;
        uint32_t last_beat;
        int32_t maxdev = 0;
        uint8_t dir = 1U;
        uint8_t prof_ok = 1U;

        if (0U == (CL_AG_PROFILES & (uint8_t)(1U << (p - 1U))))
        {
            continue;
        }
        MOTOR_ApplyProfile(MOTOR_CTRL_ALL, p);
        g_ag_status.prof = (uint32_t)p;
        RTT_DBG_Printf("[AGING] G%u start %u min travel=%ld tol=%u\r\n",
                       (unsigned)p, (unsigned)CL_AG_SEG_MIN,
                       (long)CL_AG_TRAVEL, (unsigned)CL_AG_TOL);
        last_beat = HAL_GetTick();
        t_end = HAL_GetTick() + (uint32_t)CL_AG_SEG_MIN * 60000UL;
        g_ag_status.tend = t_end;
        while ((int32_t)(t_end - HAL_GetTick()) > 0)
        {
            int32_t off = (0U != dir) ? (int32_t)CL_AG_TRAVEL :
                                        -(int32_t)CL_AG_TRAVEL;
            uint8_t i;

            if (0U == S_MoveAndWait(MOTOR_CTRL_ALL, off, AG_LEG_TIMEOUT_MS))
            {
                RTT_DBG_Printf("[AGING] G%u leg=%lu 到位超时 FAIL\r\n",
                               (unsigned)p, (unsigned long)legs);
                prof_ok = 0U;
                break;
            }
            legs++;
            g_ag_status.legs = legs;
            g_ag_status.now = HAL_GetTick();
            g_ag_status.maxdev = maxdev;
            for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
            {
                TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);
                int32_t dev;
                int32_t adev;
                uint8_t sp = 1U;
                uint8_t flt = 0U;

                if ((void *)0 == chip)
                {
                    continue;
                }
                dev = S_ReadDev(chip, &sp, &flt);
                g_ag_status.ot_seen |= (uint32_t)(flt & 0x04U);  /* 过温累计 */
                if (1U == chip->id)
                {
                    g_ag_status.dev1 = dev;
                    g_ag_status.st_flt1 = flt;
                }
                else
                {
                    g_ag_status.dev2 = dev;
                }
                adev = (dev < 0) ? -dev : dev;
                if (adev > maxdev)
                {
                    maxdev = adev;
                }
                if ((0U == sp) || (0U != flt) || (adev > (int32_t)CL_AG_TOL))
                {
                    RTT_DBG_Printf("[AGING] G%u U%u leg=%lu dev=%ld spi=%u "
                                   "flt=%02X FAIL(失步/故障)\r\n",
                                   (unsigned)p, (unsigned)chip->id,
                                   (unsigned long)legs, (long)dev,
                                   (unsigned)sp, (unsigned)flt);
                    prof_ok = 0U;
                }
            }
            if (0U == prof_ok)
            {
                break;
            }
            if ((uint32_t)(HAL_GetTick() - last_beat) >= AG_BEAT_MS)
            {
                uint8_t ot = 0U;
                uint8_t j;

                last_beat = HAL_GetTick();
                if (0U != (mask & CL_AG_TEST_THERMAL))
                {
                    for (j = 0U; j < TMC5160_CHIP_COUNT; j++)
                    {
                        TMC5160_CHIP_T *c = TMC5160_ChipAt(j);

                        if ((void *)0 != c)
                        {
                            ot |= (uint8_t)((TMC5160_ReadStatusFlags(c) & 0x04U)
                                            ? 1U : 0U);
                        }
                    }
                }
                RTT_DBG_Printf("[AGING] G%u beat legs=%lu maxdev=%ld ot=%u "
                               "now=%lu tend=%lu dir=%u\r\n",
                               (unsigned)p, (unsigned long)legs,
                               (long)maxdev, (unsigned)ot,
                               (unsigned long)HAL_GetTick(),
                               (unsigned long)t_end, (unsigned)dir);
            }
            HAL_Delay(50U);
            dir = (0U == dir) ? 1U : 0U;
        }
        MOTOR_Stop(MOTOR_CTRL_ALL);
        g_ag_status.pl_legs[p - 1U] = legs;
        g_ag_status.pl_maxdev[p - 1U] = maxdev;
        RTT_DBG_Printf("[AGING] G%u seg %s legs=%lu maxdev=%ld\r\n",
                       (unsigned)p, (0U != prof_ok) ? "PASS" : "FAIL",
                       (unsigned long)legs, (long)maxdev);
        if (0U == prof_ok)
        {
            ok = 0U;
            break;   /* 一段失败即停止整体老化 */
        }
    }
    return ok;
}

/**
 * @输出 1
 * @说明 急停：参数组 5 空转 → MOTOR_EStop(全) 关断功率级；H 级人工确认自由滑停
 */
static uint8_t S_EStop(void)
{
    RTT_DBG_Str("[ESTOP] spin(G1) -> MOTOR_EStop(all)\r\n");
    MOTOR_ApplyProfile(MOTOR_CTRL_ALL, 1U);   /* 慢档，安静 */
    MOTOR_SetVelocity(MOTOR_CTRL_ALL, (int32_t)TMC5160_GetProfileVmax(1U));
    HAL_Delay(1000U);
    MOTOR_EStop(MOTOR_CTRL_ALL);              /* 拉高 ENN 断功率级（自由停车） */
    MOTOR_Stop(MOTOR_CTRL_ALL);               /* 再停斜坡，防 XACTUAL 空跑 */
    RTT_DBG_Str("[ESTOP] ENN high (power cut) - 人工确认电机自由滑停\r\n");
    return 1U;
}

/**
 * @输出 1
 * @说明 LED_MCU(PA4) 闪 6 次（目视指示）
 */
static uint8_t S_Led(void)
{
    uint8_t k;

    for (k = 0U; k < 6U; k++)
    {
        HAL_GPIO_TogglePin(GPIOA, GPIO_PIN_4);
        HAL_Delay(150U);
    }
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, GPIO_PIN_SET);
    RTT_DBG_Str("[LED] blink done\r\n");
    return 1U;
}

/* ==== 接口实现 ==== */

void AGING_SelfTest(void)
{
    uint8_t ok = 1U;
    uint8_t stall = 0U;

    RTT_DBG_Printf("[AGING] start mask=0x%02X profiles=0x%02X seg=%u min boot=%lu\r\n",
                   (unsigned)CL_AG_TEST_MASK, (unsigned)CL_AG_PROFILES,
                   (unsigned)CL_AG_SEG_MIN, (unsigned long)WDG_BootCount());
    g_ag_status.boot = (uint32_t)WDG_BootCount();
    g_ag_status.phase = 0U;   /* 启动延时 */

    /* 回放板载留存的历史测试记录（BKPSRAM，跨复位保持） */
    NVM_Init();
    NVM_Dump();

    /* 启动延时：上电/烧录后先不动电机，留稳定窗口；期间持续喂狗 */
    {
        uint8_t k;

        RTT_DBG_Printf("[AGING] start delay %us ...\r\n",
                       (unsigned)CL_AG_START_DELAY_S);
        for (k = 0U; k < CL_AG_START_DELAY_S; k++)
        {
            WDG_Feed();
            HAL_Delay(1000U);
        }
    }
    WDG_Feed();

    if (0U != (CL_AG_TEST_MASK & CL_AG_TEST_SELFTEST))
    {
        if (0U == S_SelfTest())
        {
            ok = 0U;
            stall = 1U;   /* 通路自测失败则不进入运动老化 */
        }
    }
    if ((0U == stall) && (0U != (CL_AG_TEST_MASK & CL_AG_TEST_AGING)))
    {
        g_ag_status.phase = 2U;   /* aging */
        if (0U == S_Aging(CL_AG_TEST_MASK))
        {
            ok = 0U;
        }
    }
    if (0U != (CL_AG_TEST_MASK & CL_AG_TEST_ESTOP))
    {
        g_ag_status.phase = 3U;   /* estop */
        (void)S_EStop();
    }
    if (0U != (CL_AG_TEST_MASK & CL_AG_TEST_LED))
    {
        g_ag_status.phase = 4U;   /* led */
        (void)S_Led();
    }
    g_ag_status.phase = 5U;       /* done */
    RTT_DBG_Printf("[AGING RESULT] %s mask=0x%02X\r\n",
                   (0U != ok) ? "PASS" : "FAIL", (unsigned)CL_AG_TEST_MASK);

    /* 写入板载留存（BKPSRAM 环形，跨复位保持） */
    {
        nvm_result_t rec;
        uint8_t i;

        rec.seq = 0U;
        rec.boot = (uint32_t)WDG_BootCount();
        rec.st_ok = ok;
        rec.ot_seen = g_ag_status.ot_seen;
        for (i = 0U; i < NVM_PROFILE_COUNT; i++)
        {
            rec.legs[i] = g_ag_status.pl_legs[i];
            rec.maxdev[i] = g_ag_status.pl_maxdev[i];
        }
        NVM_Append(&rec);
        RTT_DBG_Printf("[NVM] stored #%lu\r\n", (unsigned long)(NVM_Count() - 1U));
    }
}

#endif /* CL_AGING_TEST */
