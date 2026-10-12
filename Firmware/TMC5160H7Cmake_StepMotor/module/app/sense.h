/*****************************************************************************
 * @文件: sense.h
 * @作者: cl
 * @日期: 2026-09-22
 * @版本: v1.0
 * @说明: 到位跟踪与终态 mailbox（摆法2）
 *        2026-09-23 删服务 FIFO（PostAsync/RequestSync/Serve/Overrun）——
 *        运动执行由任务A经 motor_ctrl 直调，无中间层；
 *        到位判定（Check）改纯读 sched 快照（任务B调用，无 SPI）
 *        终态 done 槽：任务B写、任务A取走发送（CAN TX 唯一写者=任务A）
 * @依赖: app/sched(快照), drv/tmc5160(芯片数), app/motor_ctrl(编号)
 ****************************************************************************/
#ifndef SENSE_H
#define SENSE_H

#include <stdint.h>

/* ==== 终态 mailbox 项（任务B写、任务A读，每电机一槽，按 motor 匹配） ==== */
typedef struct {
    volatile uint8_t active;
    volatile uint8_t motor;
    volatile uint8_t cmd;      /* 回显命令码（到位反馈 Can[0]） */
    volatile uint8_t param;    /* 回显参数组（到位反馈 Can[2]） */
    volatile uint8_t arrived;  /* 1=到位，0=超时/失败（到位反馈 Can[3]） */
} SENSE_DONE_T;

/* 到位跟踪：Arm/Disarm（CAN 分发在任务A调用） */
void    SENSE_ArmCompletion(uint8_t motor, uint8_t cmd, uint8_t param,
                            int32_t t1, int32_t t2);
void    SENSE_Disarm(uint8_t motor);
/* 到位检测（仅任务B调用；纯读 sched 快照，命中投 done 槽，不发 CAN） */
void    SENSE_CheckCompletion(void);
/* 取终态（任务A；返回 1=取到并清槽） */
uint8_t SENSE_TakeDone(uint8_t motor, SENSE_DONE_T *out);

#endif /* SENSE_H */
