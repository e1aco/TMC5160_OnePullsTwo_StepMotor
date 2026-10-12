/*****************************************************************************
 * @文件: motor_ctrl.h
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: 电机控制中间层（封装电机选择 + 运动执行）
 *        摆法2：仅任务A调用，直调 drv；读接口已删（改 sched 快照）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一，行为等价
 * @依赖: drv/tmc5160（原 tmc5160_usr，2026-09-09 文件合并）
 ****************************************************************************/
#ifndef MOTOR_CTRL_H
#define MOTOR_CTRL_H

#include <stdint.h>
#include "drv/tmc5160.h"

/* ==== 电机位掩码（协议 byte[5]；bit(i)=电机 i+1，接收/反馈统一） ====
 * 0x01=电机1, 0x02=电机2, 0x03=电机1+2, 0x04=电机3 … */
#define MOTOR_CTRL_U1   0x01U
#define MOTOR_CTRL_U2   0x02U
#define MOTOR_CTRL_ALL  ((1U << TMC5160_CHIP_COUNT) - 1U)   /* 2电机=0x03 */
#define MOTOR_CTRL_VALID_MASK  MOTOR_CTRL_ALL

/* id→单电机位；掩码合法性：非 0 且无表外位 */
#define MOTOR_MASK_BIT(id)     (1U << ((id) - 1U))
#define MOTOR_MASK_VALID(m)    ((0U != (m)) && \
                                (0U == ((uint8_t)(m) & (uint8_t)~MOTOR_CTRL_VALID_MASK)))

/* ==== 运动参数组 ID ==== */
#define MOTION_GROUP_1  0x01
#define MOTION_GROUP_2  0x02
#define MOTION_GROUP_3  0x03
#define MOTION_GROUP_4  0x04
#define MOTION_GROUP_5  0x05   /* 600rpm 生产高速档 */
#define MOTION_GROUP_6  0x06   /* 1200rpm 极限运行档（生产禁用，见 tmc5160.c 组6注记） */

/* ==== 接口（写操作：仅任务A调用，内部直调 SPI） ==== */
void           MOTOR_Init(void);
TMC5160_CHIP_T *MOTOR_GetChip(uint8_t motor);
void           MOTOR_MoveTo(uint8_t motor, int32_t target);
void           MOTOR_MoveBy(uint8_t motor, int32_t offset);
void           MOTOR_SetVelocity(uint8_t motor, int32_t velocity);
void           MOTOR_Stop(uint8_t motor);
void           MOTOR_EStop(uint8_t motor);
void           MOTOR_ApplyProfile(uint8_t motor, uint8_t group);

#endif /* MOTOR_CTRL_H */

