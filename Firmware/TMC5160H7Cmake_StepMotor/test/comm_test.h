/*****************************************************************************
 * @文件: comm_test.h
 * @作者: cl
 * @日期: 2026-09-08
 * @版本: v1.1
 * @说明: CAN 收发计数最小单元（2026-09-22 清场：SPI 判据钩子声明删除）
 * @依赖: drv/uart_dbg, drv/rtt_dbg, algo/queue
 ****************************************************************************/
#ifndef COMM_TEST_H
#define COMM_TEST_H

#include <stdint.h>

void COMM_Test_OnCanRxISR(void);
void COMM_Test_CAN_Heartbeat(void);

#endif /* COMM_TEST_H */
