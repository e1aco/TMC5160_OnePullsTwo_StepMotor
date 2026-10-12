/*****************************************************************************
 * @文件: wdg.c
 * @作者: cl
 * @日期: 2026-09-20
 * @版本: v2.0
 * @说明: IWDG 驱动层 = HAL 封装（启动由 CubeMX MX_IWDG1_Init 完成；
 *        本层只做复位源锁存/打印/清标志 + 运行期喂狗 + 状态查询）
 * @平台: STM32H750VBT6 (IWDG1, /128 + 1249 ≈ 5s，见 Core/Src/iwdg.c)
 * @依赖: HAL_IWDG(Core/Src/iwdg.c hiwdg1), drv/rtt_dbg, drv/uart_dbg
 ****************************************************************************/
#include "wdg.h"
#include "stm32h7xx_hal.h"
#include "iwdg.h"
#include "hal/rtt_dbg.h"
#include "hal/uart_dbg.h"

/* 本次复位源锁存（Init 首行读 RSR，RMVF 清零前保存） */
static uint8_t s_was_iwdg = 0;

/* IWDG 同步诊断标志（1=WDG_Init 时 PVU/RVU/WVU 全清；0=同步告警但仍在喂狗）
 * 2026-09-22：不再门控喂狗（原"降级不喂"会致 5s 复位环），仅供诊断 */
static uint8_t s_dog_on = 0;

/* 复位计数魔数（BKP0R 高 16 位）：POR 后 BKP0R=0（魔数不符→首启计 1）；
 * 系统复位（IWDG/软件/引脚）保留备份域 → 计数 +1 */
#define WDG_BOOT_MAGIC  0x5A5A0000UL

/* 本次上电会话复位次数（Init 中自 BKP0R 读出并自增） */
static uint32_t s_boot_count = 0;

/* ==== 接口函数 ==== */

/**
 * @说明 锁存复位源并打印→清标志→确认 HAL 已启动→喂一次；
 *        同步异常只告警不停喂（MX 已启动 IWDG，停喂=复位环，见下方修正注）
 * @注意 硬件启动在 generated MX_IWDG1_Init（HAL 官方序列：先 0xCCCC 强制 LSI，
 *        再配 PR/RLR，失败进 Error_Handler）；本函数须在 RTT/UART 初始化后调用
 * 依据 Core/Src/iwdg.c（CubeMX）：Prescaler=/128 + Reload=1249 + Window=4095
 * 依据 .cl/memory/config.md profile5 推导：要求超时 > 喂狗最大间隔 400ms
 * → 1250/250Hz=5.0s（LSI 标称 32kHz，待实测确认）；卡死复位实测为功能判据
 */
void WDG_Init(void)
{
    uint32_t rsr;

    /* 首行锁存复位源（RMVF 清零前） */
    rsr = RCC->RSR;
    s_was_iwdg = (0U != (rsr & RCC_RSR_IWDG1RSTF)) ? 1U : 0U;
    if (0U != s_was_iwdg)
    {
        RTT_DBG_Str("[WDG] last reset=IWDG (jam test PASS)\r\n");
        UART_DBG_Str("[WDG] last reset=IWDG (jam test PASS)\r\n");
    }
    else
    {
        RTT_DBG_Str("[WDG] reset src=POR/PIN (first boot)\r\n");
        UART_DBG_Str("[WDG] reset src=POR/PIN (first boot)\r\n");
    }

    /* 清复位标志（写 RMVF，不影响备份域） */
    RCC->RSR |= RCC_RSR_RMVF;

    /* 复位计数：RTC 备份寄存器 BKP0R 跨系统复位持久（POR 清零→首启=1）
     * 依据 RM0433(STM32H7) RTC BKPxR（备份域）：须 RTC APB 时钟 + PWR DBP
     *   才可访问（HAL_PWR_EnableBkUpAccess 置 PWR_CR1.DBP）
     * 用途：老化全程 IWDG/异常复位次数统计（首启=1） */
    __HAL_RCC_RTC_CLK_ENABLE();
    HAL_PWR_EnableBkUpAccess();
    {
        uint32_t v = RTC->BKP0R;

        if ((v & 0xFFFF0000UL) != WDG_BOOT_MAGIC)
        {
            v = WDG_BOOT_MAGIC | 1UL;               /* POR 首启 */
        }
        else
        {
            v = WDG_BOOT_MAGIC | (((v & 0xFFFFUL) + 1UL) & 0xFFFFUL);
        }
        RTC->BKP0R = v;
        s_boot_count = v & 0xFFFFUL;
    }

    /* 确认 HAL 启动已同步（PVU/RVU/WVU 全清）
     * 2026-09-22 修正：MX_IWDG1_Init 无论 SR 是否同步都已启动 IWDG，故原
     * "同步异常→降级不喂狗"会变成 5s 复位环；改为恒喂（对未启动的狗写 KR
     * 亦无害），s_dog_on 仅作同步诊断标志，不再门控喂狗 */
    s_dog_on = (0U == (IWDG1->SR & (IWDG_SR_WVU | IWDG_SR_RVU | IWDG_SR_PVU))) ? 1U : 0U;
    if (0U == s_dog_on)
    {
        RTT_DBG_Printf("[WDG WARN] HAL sync SR=%08lX, feed anyway\r\n",
                       (unsigned long)IWDG1->SR);
        UART_DBG_Str("[WDG WARN] HAL sync stuck, feed anyway\r\n");
    }
    WDG_Feed();
    RTT_DBG_Str("[WDG] IWDG on (HAL, ~5s nominal)\r\n");
    UART_DBG_Str("[WDG] IWDG on (HAL, ~5s nominal)\r\n");
}

/**
 * @说明 经 HAL 重装载 IWDG 计数器（KR=0xAAAA；HAL 无 SR 等待，可放分片循环）
 */
void WDG_Feed(void)
{
    (void)HAL_IWDG_Refresh(&hiwdg1);
}

/* 返回上电锁存的复位源（1=IWDG 复位） */
uint8_t WDG_WasIwdgReset(void)
{
    return s_was_iwdg;
}

/* 返回 IWDG 启动同步诊断标志（1=同步确认；0=同步告警，仍正常喂狗） */
uint8_t WDG_IsOn(void)
{
    return s_dog_on;
}

/* 返回本次上电会话复位次数（RTC BKP0R 跨复位持久；POR 首启=1） */
uint32_t WDG_BootCount(void)
{
    return s_boot_count;
}
