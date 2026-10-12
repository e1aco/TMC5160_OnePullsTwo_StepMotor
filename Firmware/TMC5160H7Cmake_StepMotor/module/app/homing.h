/*****************************************************************************
 * @文件: homing.h
 * @作者: cl
 * @日期: 2026-09-12
 * @版本: v1.0
 * @说明: 无编码器回零/校准编排层（StallGuard2 堵转碰硬限位）——状态机/时序/CAN 反馈；
 *        芯片级 SG 原语在 drv/tmc5160（TMC5160_Home*）
 *        2026-09-24 重构：读全部走 app/sched 快照（回零期零 SPI 直读）；
 *        按电机数组化（支持位掩码并发回零）；电机级校准状态
 *        （未校准/校准中/已校准）驱动运动门控
 * @依赖: drv/tmc5160, app/sched(快照), app/motor_ctrl(掩码), app/closed_loop
 ****************************************************************************/
#ifndef HOMING_H
#define HOMING_H

#include <stdint.h>
#include "drv/tmc5160.h"   /* 方向宏 TMC5160_HOME_DIR_x 与速度 TMC5160_HOME_VMAX */

/* ==== 电机级校准状态（bit(i)=电机 i+1，按芯片表下标存） ==== */
typedef enum {
    HOME_ST_UNCALIBRATED = 0,   /* 未校准：位置基准丢失，禁止运动，须回零 */
    HOME_ST_RUN,                /* 校准中-寻零（速度模式撞限位） */
    HOME_ST_BACKOFF,            /* 校准中-反向回退 */
    HOME_ST_CALIBRATED          /* 已校准：正常执行 */
} HOME_STATE_T;

/* ==== 接口（均仅任务A调用，SPI 上下文） ==== */

/* 初始化：所有电机置未校准（osKernelStart 前调用） */
void    HOME_Init(void);

/* 上电免回零（调试/无挡板台架用）：将当前上电位置设为零点（XACTUAL/X_ENC
 * 清零）并置已校准；仅 osKernelStart 前调用（单线程 SPI）。
 * 删除 main.c 中本调用即恢复"上电未校准、须回零"模式 */
void    HOME_ZeroAtPowerUp(void);

/* 启动回零（掩码并发；非阻塞；param=回显参数组）；0=已启动，1=忙/参数非法 */
uint8_t HOME_Start(uint8_t motor_mask, uint8_t dir, uint8_t param);

/* 主循环每圈调用，推进各电机状态机；终态发 CAN（成功 DONE / 失败未校准错误帧） */
void    HOME_Tick(void);

/* 中止掩码内回零电机并置未校准（不发帧；调用方发"正在校准中"错误帧） */
void    HOME_Abort(uint8_t motor_mask);

/* 置未校准（急停等；中止回零并恢复寄存器现场，不发帧） */
void    HOME_MarkUncalibrated(uint8_t motor_mask);

/* 查询：掩码内是否有校准中 / 未校准 / 全部已校准 */
uint8_t HOME_IsCalibrating(uint8_t motor_mask);
uint8_t HOME_IsUncalibrated(uint8_t motor_mask);
uint8_t HOME_IsCalibrated(uint8_t motor_mask);

/* 单电机状态查询（非法电机返未校准） */
HOME_STATE_T HOME_GetState(uint8_t motor);

#endif /* HOMING_H */
