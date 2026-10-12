/*****************************************************************************
 * @文件: tim_test.h
 * @作者: cl
 * @日期: 2026-09-08
 * @版本: v1.0
 * @说明: 时序探针与逻辑仪触发 (Saleae)
 * @依据: details/probe.md 双版本宏，量产可剔除 TEST_TIM_*
 * @依赖: 无 (生产版空宏零开销)
 ****************************************************************************/
#ifndef TIM_TEST_H
#define TIM_TEST_H

#include <stdint.h>

#ifdef CL_TIMING_MEASURE
void TEST_TIM_Init(void);
void TEST_TIM_Start(uint8_t tag);
void TEST_TIM_Stop(uint8_t tag);
void TEST_TIM_Toggle(void);
void TEST_TIM_CalibToggle(void);
#else
#define TEST_TIM_Init()          ((void)0)
#define TEST_TIM_Start(tag)      ((void)0)
#define TEST_TIM_Stop(tag)       ((void)0)
#define TEST_TIM_Toggle()        ((void)0)
#define TEST_TIM_CalibToggle()   ((void)0)
#endif

/* 触发 IO: PD14 TMC_CLK 复用为 Saleae 同步点 (与 TIM4 CH3 复用, 推挽输出) */
void TEST_SPI_SaleaeTriggerInit(void);
void TEST_SPI_SaleaePulse(void);

#endif /* TIM_TEST_H */
