/*****************************************************************************
 * @文件: sched.h
 * @作者: cl
 * @日期: 2026-09-22
 * @版本: v1.0
 * @说明: RTOS 任务间数据交换层（SPI 采集快照）
 *        摆法2：SPI 总线只由任务A(senseTask) 触碰，任务B/遥测只读 RAM 快照
 *        （S_SpiTransfer 为裸寄存器轮询、无互斥，并发会腰斩事务）
 * @依据: RTOS 移植裁决 2026-09-22（用户：选 c + 标待实测）+ 2026-09-23 摆法2
 * @依赖: drv/tmc5160, app/motor_ctrl
 * @待实测: ①1ms 快照滞后对闭环稳定性（G6 20rev/s 下 1ms=1024µsteps>容差512）
 *          ②快照一致性（XACTUAL/X_ENC 非同刻采样，deviation 是否失真）
 ****************************************************************************/
#ifndef SCHED_H
#define SCHED_H

#include <stdint.h>
#include "drv/tmc5160.h"   /* TMC5160_CHIP_COUNT（快照槽数单源） */

/* 快照槽数（= 芯片表行数，单源 TMC5160_CHIP_COUNT；增减芯片零改动） */
#define SCHED_MOTOR_COUNT   TMC5160_CHIP_COUNT

/* ==== 电机状态快照（ctrlTask 写，其他任务只读） ==== */
typedef struct {
    int32_t  x_actual;    /* XACTUAL  (0x21) 斜坡实际位置 */
    int32_t  x_enc;       /* X_ENC    (0x39) 编码器位置(已归零) */
    int32_t  vactual;     /* VACTUAL  (0x22) 实际速度 */
    uint32_t ramp_stat;   /* RAMP_STAT(0x35) */
    uint32_t drv_status;  /* DRVSTATUS(0x6F) */
    uint32_t gstat;       /* GSTAT    (0x01) */
    uint32_t enc_status;  /* ENC_STATUS(0x3B) */
    uint8_t  diag_valid;  /* 1=诊断字段已采集过至少一次 */
} SCHED_SNAP_T;

/* ==== 接口 ==== */

/* 初始化快照（osKernelStart 前调用） */
void    SCHED_Init(void);

/* 快采：XACTUAL + X_ENC + VACTUAL（仅 S_SenseTask 调用；内部经 SPI 读，
 * 禁止其他任务调用；vactual 供任务B到位判定速度归零条件） */
void    SCHED_CollectFast(void);

/* 诊断采集：RAMP_STAT/DRVSTATUS/GSTAT/ENC_STATUS（VACTUAL 已入快采）
 * （仅 S_SenseTask 调用，1Hz 节流；内部经 SPI 读） */
void    SCHED_CollectDiag(void);

/* 回零兴趣门控：置位后该电机 ramp_stat/drv_status/gstat 随快采 2ms 采集
 * （回零期状态机改读快照，零直读；终态清位；仅任务A调用） */
void    SCHED_SetHoming(uint8_t motor, uint8_t on);

/* 快照只读（任意任务；纯 RAM 读。diag 字段可能在采集瞬间被撕裂，诊断用途可接受） */
const SCHED_SNAP_T *SCHED_Snap(uint8_t motor);
int32_t SCHED_GetActual(uint8_t motor);
int32_t SCHED_GetEncoder(uint8_t motor);

#endif /* SCHED_H */
