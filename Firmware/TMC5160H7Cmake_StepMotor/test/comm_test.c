/*****************************************************************************
 * @文件: comm_test.c
 * @作者: cl
 * @日期: 2026-09-08
 * @版本: v1.1
 * @说明: CAN 收发计数最小单元（ISR 计数 + Heartbeat 1Hz 回显）
 *  2026-09-22 清场：SPI 判据钩子 6 个（SPI/Soak/Quiet/ManualRun/ProdRun/
 *  ProdRun_U1）归档删（长链见 log.md，git 兜底）；判据改走 PCAN 脚本
 * @依赖: drv/uart_dbg, drv/rtt_dbg, algo/queue
 ****************************************************************************/
#include "comm_test.h"
#include "hal/uart_dbg.h"
#include "hal/rtt_dbg.h"
#include "algo/queue.h"

static volatile uint32_t s_can_rx_cnt = 0;
static uint32_t s_can_rx_cnt_shadow = 0;

/* ==== CAN 心跳回显 ==== */

/**
 * @说明 供 stm32h7xx_it.c HAL_FDCAN_RxFifo0Callback 计数钩子调用
 *        在 ISR 中仅原子自增，不做阻塞/打印
 */
void COMM_Test_OnCanRxISR(void)
{
    s_can_rx_cnt++;
}

/**
 * @说明 主循环 1Hz 调用，回显 CAN RX 计数 + 队列深度到 USART1+RTT
 * @判据: "[CAN RX OK] cnt=..." 出现即 CAN 接收通路正常
 *        外置 PCAN 发 0x1AA55F42 后 cnt 递增即过滤器+波特率正确
 */
void COMM_Test_CAN_Heartbeat(void)
{
    uint32_t cnt = s_can_rx_cnt;

    /* 原子读 (单32位, Cortex-M7 自然对齐, 无需临界区) */
    if (cnt != s_can_rx_cnt_shadow)
    {
        s_can_rx_cnt_shadow = cnt;
        UART_DBG_Printf("[CAN RX OK] cnt=%u q_empty=%u\r\n",
                        (unsigned)cnt, (unsigned)QUEUE_IsEmpty(&g_queue_st));
        RTT_DBG_Printf("[CAN RX OK] cnt=%u q_empty=%u\r\n",
                       (unsigned)cnt, (unsigned)QUEUE_IsEmpty(&g_queue_st));
    }
}
