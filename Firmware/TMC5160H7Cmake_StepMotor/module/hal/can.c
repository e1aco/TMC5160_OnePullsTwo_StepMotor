/*****************************************************************************
 * @文件: can.c
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: CAN 驱动层 = 硬件原语 + 反馈组帧发送
 *        2026-09-23 协议分发 CAN_Process 迁 app/can_cmd.c（消反向依赖豁免中的
 *        分发/执行部分；本文件不再 include app 层头文件）；
 *        2026-09-22 senseTask 单 SPI owner：本文件无 SPI 直读
 * @来源: 自 TMC5160_StepMotor(F407 bxCAN) 移植归一，API 重写为 H7 FDCAN HAL
 * @变更点: bxCAN FilterBank/AddTxMessage/GetTxMailboxesFreeLevel →
 *          FDCAN Filter/AddMessageToTxFifoQ/GetTxFifoFreeLevel；
 *          RX 中断回调 HAL_CAN_RxFifo0MsgPendingCallback →
 *          HAL_FDCAN_RxFifo0Callback（实现在 stm32h7xx_it.c）
 * @注意: MessageRAM 分配在 Core/Src/fdcan.c MX_FDCAN2_Init 中完成
 *        （ExtFiltersNbr=1 / RxFifo0ElmtsNbr=8 / TxFifoQueueElmtsNbr=3），
 *        CubeMX 重生成 .ioc 时需保留该配置
 * @平台: STM32H750VBT6 (FDCAN2, 经典帧 500kbit/s)
 * 依据 require.md 时钟树: FDCANCLK=8MHz(HSE 直接), Prescaler=1, Seg1=13,
 *   Seg2=2 → (1+13+2)TQ=16TQ → 8MHz/16=500kbit/s
 * @依赖: HAL_FDCAN
 ****************************************************************************/
#include "hal/can.h"

/* TX FIFO 深度，与 fdcan.c TxFifoQueueElmtsNbr 保持一致 */
#define CAN_TX_FIFO_DEPTH  3

/* ==== 内部函数 ==== */

/**
 * @输入 无
 * @输出 无
 * @说明 配置扩展帧过滤器，仅接收 ID 0x1AA55F42 到 RX FIFO0
 */
static void S_FilterConfig(void)
{
    FDCAN_FilterTypeDef filter;

    filter.IdType = FDCAN_EXTENDED_ID;
    filter.FilterIndex = 0;
    filter.FilterID1 = CAN_RX_ID;
    filter.FilterID2 = 0x1FFFFFFF;             /* 掩码: 29位全比对,精确匹配 RX_ID */
    filter.FilterType = FDCAN_FILTER_MASK;
    filter.FilterConfig = FDCAN_FILTER_TO_RXFIFO0;

    if (HAL_FDCAN_ConfigFilter(&hfdcan2, &filter) != HAL_OK)
    {
        Error_Handler();
    }

    /* 未匹配帧默认丢弃（不进 FIFO），拒绝远程帧 */
    if (HAL_FDCAN_ConfigGlobalFilter(&hfdcan2, FDCAN_REJECT, FDCAN_REJECT,
                                     FDCAN_FILTER_REMOTE,
                                     FDCAN_FILTER_REMOTE) != HAL_OK)
    {
        Error_Handler();
    }
}

/**
 * @输入 id: 发送帧 ID(扩展帧); data: 8字节数据指针
 * @输出 0=成功, 1=发送失败
 * @说明 封装 HAL_FDCAN_AddMessageToTxFifoQ，非阻塞发送
 */
static uint8_t S_SendMsg(uint32_t id, uint8_t *data)
{
    FDCAN_TxHeaderTypeDef tx_header;

    tx_header.Identifier = id;
    tx_header.IdType = FDCAN_EXTENDED_ID;
    tx_header.TxFrameType = FDCAN_DATA_FRAME;
    tx_header.DataLength = FDCAN_DLC_BYTES_8;
    tx_header.ErrorStateIndicator = FDCAN_ESI_ACTIVE;
    tx_header.BitRateSwitch = FDCAN_BRS_OFF;
    tx_header.FDFormat = FDCAN_CLASSIC_CAN;
    tx_header.TxEventFifoControl = FDCAN_NO_TX_EVENTS;
    tx_header.MessageMarker = 0;

    if (HAL_FDCAN_AddMessageToTxFifoQ(&hfdcan2, &tx_header, data) != HAL_OK)
    {
        return 1;
    }
    return 0;
}

/**
 * @输入 无
 * @输出 无
 * @说明 启动 CAN 并使能 RX FIFO0 新报文中断（IT0 中断线）
 */
static void S_StartRx(void)
{
    if (HAL_FDCAN_Start(&hfdcan2) != HAL_OK)
    {
        Error_Handler();
    }
    if (HAL_FDCAN_ActivateNotification(&hfdcan2,
                                       FDCAN_IT_RX_FIFO0_NEW_MESSAGE |
                                       FDCAN_IT_RX_FIFO0_MESSAGE_LOST,
                                       0U) != HAL_OK)
    {
        Error_Handler();
    }
}

/**
 * @输入 data: 8字节原始数据
 * @输出 uint8_t: 校验和
 * @说明 前 7 字节累加取低 8 位
 */
static uint8_t S_CalcChecksum(uint8_t *data)
{
    uint8_t sum = 0;
    uint8_t i;

    for (i = 0; i < 7; i++)
    {
        sum += data[i];
    }
    return sum;
}

/* ==== 驱动原语 ==== */

/**
 * @输入 无
 * @输出 无
 * @说明 CAN 驱动初始化：过滤器 + 启动中断接收
 */
void CAN_Init(void)
{
    S_FilterConfig();
    S_StartRx();
}

/* ==== 反馈组帧发送（用户协议 v2.1；纯组帧无 SPI，任务A调用） ==== */

/**
 * @输入 cmd: 命令码（Can[0] 回显）
 * @输出 0=成功, 1=发送失败
 * @说明 发送 ACK (ID: 0x1AA55F43)；Can[0]=命令码, Can[1..6]=0
 */
uint8_t CAN_SendAckRaw(uint8_t cmd)
{
    uint8_t tx_data[8];

    tx_data[0] = cmd;
    tx_data[1] = 0U;
    tx_data[2] = 0U;
    tx_data[3] = 0U;
    tx_data[4] = 0U;
    tx_data[5] = 0U;
    tx_data[6] = 0U;
    tx_data[7] = S_CalcChecksum(tx_data);

    return S_SendMsg(CAN_TX_ID, tx_data);
}

/**
 * @输入 cmd/motor/param: 回显命令码/单电机位/参数组;
 *        status: 状态位组合（CAN_ST_ARRIVED/STALL/OTW/DRVERR/SPIFAIL，见 can.h）
 * @输出 0=成功, 1=发送失败
 * @说明 发送到位反馈 (ID: 0x1AA55F43)；Can[3..5] 由 status 位派生（单源），
 *        Can[6]=status 全 5 位（v2.1：补回失步位 bit1 与 SPI 异常位 bit4）
 */
uint8_t CAN_SendArrivalRaw(uint8_t cmd, uint8_t motor, uint8_t param,
                           uint8_t status)
{
    uint8_t tx_data[8];

    tx_data[0] = cmd;
    tx_data[1] = motor;
    tx_data[2] = param;
    tx_data[3] = (0U != (status & CAN_ST_ARRIVED)) ? 1U : 0U;
    tx_data[4] = (0U != (status & CAN_ST_OTW)) ? 1U : 0U;
    tx_data[5] = (0U != (status & CAN_ST_DRVERR)) ? 1U : 0U;
    tx_data[6] = status;
    tx_data[7] = S_CalcChecksum(tx_data);

    return S_SendMsg(CAN_TX_ID, tx_data);
}

/**
 * @输入 cmd: 命令码(0x09); motor: 单电机位; status: 状态位组合(CAN_ST_*);
 *        pos: XACTUAL（µsteps，无效时上层置 CAN_ST_SPIFAIL）
 * @输出 0=成功, 1=发送失败
 * @说明 发送位置反馈 (ID: 0x1AA55F43)；Can[3..6]=pos int32 大端
 *        （与命令帧 value 同为大端，见 require.md 附录A）
 */
uint8_t CAN_SendPosRaw(uint8_t cmd, uint8_t motor, uint8_t status, int32_t pos)
{
    uint8_t tx_data[8];
    uint32_t u = (uint32_t)pos;

    tx_data[0] = cmd;
    tx_data[1] = motor;
    tx_data[2] = status;
    tx_data[3] = (uint8_t)((u >> 24) & 0xFFU);
    tx_data[4] = (uint8_t)((u >> 16) & 0xFFU);
    tx_data[5] = (uint8_t)((u >> 8) & 0xFFU);
    tx_data[6] = (uint8_t)(u & 0xFFU);
    tx_data[7] = S_CalcChecksum(tx_data);

    return S_SendMsg(CAN_TX_ID, tx_data);
}

/**
 * @输入 err_code: 错误码(见 can.h 错误码表)
 * @输出 0=成功, 1=发送失败
 * @说明 发送错误帧 (ID: 0x1AA55F43)；Can[0..6] 全=错误码
 */
uint8_t CAN_SendErrorRaw(uint8_t err_code)
{
    uint8_t tx_data[8];

    tx_data[0] = err_code;
    tx_data[1] = err_code;
    tx_data[2] = err_code;
    tx_data[3] = err_code;
    tx_data[4] = err_code;
    tx_data[5] = err_code;
    tx_data[6] = err_code;
    tx_data[7] = S_CalcChecksum(tx_data);

    return S_SendMsg(CAN_TX_ID, tx_data);
}
