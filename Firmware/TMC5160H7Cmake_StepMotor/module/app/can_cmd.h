/*****************************************************************************
 * @文件: can_cmd.h
 * @作者: cl
 * @日期: 2026-09-23
 * @版本: v1.0
 * @说明: CAN 命令分发与反馈组装（app 编排层）
 *        用户协议 v2.1（见 require.md 附录A）：命令帧 Can[0]=cmd/Can[1]=motor
 *        位掩码/Can[2]=param/Can[3..6]=value 大端；反馈 ACK/到位/位置(0x09)/错误帧
 *        摆法2：仅任务A调用 CAN_CMD_Process；就地执行 + 即时反馈
 * @依赖: drv/can(收发原语), drv/tmc5160, app/motor_ctrl, app/closed_loop,
 *        app/homing, app/sense(Arm/Disarm), app/sched(快照)
 ****************************************************************************/
#ifndef CAN_CMD_H
#define CAN_CMD_H

#include <stdint.h>

/* 从 RX 队列取一条命令解析执行并发反馈（仅任务A调用） */
void CAN_CMD_Process(void);

#endif /* CAN_CMD_H */
