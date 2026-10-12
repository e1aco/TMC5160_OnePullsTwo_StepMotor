/*****************************************************************************
 * @文件: tim_test.c
 * @作者: cl
 * @日期: 2026-09-08
 * @版本: v1.0
 * @说明: 时序探针实现 (DWT CYCCNT, H7 Cortex-M7)
 * @依据: details/probe.md DWT 序列，CPU=480MHz(D1CPRE=1)
 * @依赖: HAL, DWT
 ****************************************************************************/
#include "tim_test.h"
#include "main.h"

#ifdef CL_TIMING_MEASURE
#include <stdio.h>

/* ==== DWT 时序探针 ==== */

/* DWT 使能 */
void TEST_TIM_Init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static uint32_t s_start_cy[16];

void TEST_TIM_Start(uint8_t tag)
{
    if (tag < 16)
    {
        s_start_cy[tag] = DWT->CYCCNT;
    }
}

void TEST_TIM_Stop(uint8_t tag)
{
    uint32_t end, diff, us;
    if (tag >= 16)
    {
        return;
    }
    end = DWT->CYCCNT;
    diff = end - s_start_cy[tag];
    /* DWT CYCCNT 计数钟 = CPU 时钟 = SYSCLK = 480MHz(D1CPRE=DIV1; HCLK=240MHz 是总线钟)
     * 依据 .cl/memory/config.md stm32_sysclk=480MHz + OpenOCD 实测 962.5Mcyc/2s≈481MHz
     * → us = cycles / 480 */
    us = diff / 480U;
    /* 回传 [TM] tag us cycles */
    printf("[TM] %u %u us %u\r\n", (unsigned)tag, (unsigned)us, (unsigned)diff);
}

void TEST_TIM_Toggle(void)
{
    HAL_GPIO_TogglePin(GPIOD, GPIO_PIN_14);
}

void TEST_TIM_CalibToggle(void)
{
    /* 100×单位翻转一次，供 probe.md 校准 (半周期100us) */
    for (uint8_t i = 0; i < 100; i++)
    {
        __NOP();
    }
    HAL_GPIO_TogglePin(GPIOD, GPIO_PIN_14);
}

#else
/* 生产版空实现由头文件宏覆盖，此处无需代码 */
#endif

/* ==== Saleae 触发 (PA4 LED_MCU) ==== */
/* Saleae 触发: PD14 复用已由 TIM4 占用，故用 PA4 LED_MCU 作独立触发 */
void TEST_SPI_SaleaeTriggerInit(void)
{
    /* PA4 已在 MX_GPIO_Init 为推挽输出，初始高 */
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, GPIO_PIN_SET);
}

void TEST_SPI_SaleaePulse(void)
{
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, GPIO_PIN_RESET);
    /* 10us 窄脉冲 (DWT 240MHz) */
    {
        uint32_t s = DWT->CYCCNT;
        while ((DWT->CYCCNT - s) < (10U * 240U))
        {
            __NOP();
        }
    }
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, GPIO_PIN_SET);
}
