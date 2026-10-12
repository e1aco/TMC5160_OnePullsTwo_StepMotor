/*****************************************************************************
 * @文件: tasks.h
 * @作者: cl
 * @日期: 2026-09-22
 * @版本: v1.0
 * @说明: RTOS 任务层（任务定义/创建）——FreeRTOS + CMSIS-RTOS2 封装
 *        任务划分依据 RTOS 移植裁决 2026-09-22 + 2026-09-23 摆法2：
 *          任务A senseTask   High(40)  2ms  CAN分发+快照+闭环Tick+回零+终态发帧
 *                                          （SPI 唯一 owner，CAN TX 唯一写者）
 *          任务B ctrlTask    28        1ms  仅到位判定（纯读快照，无SPI无CAN）
 *          tmrTask           BelowNormal(16) 1s   1Hz 遥测回传（只读快照）
 *          wdgTask           Low(8)         200ms IWDG 喂狗（最低优先级=卡死即停喂）
 * @依据: .cl/memory/config.md（FreeRTOS 段）；app/sched.h + 2026-09-23 摆法2
 * @依赖: cmsis_os2, app 层各模块, drv 层各模块
 ****************************************************************************/
#ifndef TASKS_H
#define TASKS_H

/* 创建全部任务（osKernelInitialize 之后、osKernelStart 之前调用） */
void TASKS_Init(void);

#endif /* TASKS_H */
