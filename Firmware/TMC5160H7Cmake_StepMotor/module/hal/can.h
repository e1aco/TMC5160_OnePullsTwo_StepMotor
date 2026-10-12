/*****************************************************************************
 * @文件: can.h
 * @作者: cl
 * @日期: 2026-09-09
 * @版本: v1.0
 * @说明: CAN 驱动层统一头 = 原 can_drv.h + can_usr.h 文件合并
 * @来源: 自 TMC5160_StepMotor(F407 bxCAN) 移植归一，重写为 H7 FDCAN HAL
 * @平台: STM32H750VBT6 (FDCAN2 PB12/PB13, 经典帧 500kbit/s)
 * @协议: 用户协议 v2.1（v2 2026-09-24 确认稿 + 2026-09-30 追加 0x09 读位置与
 *        到位帧 Can[6] 状态位；见 require.md 附录A）：
 *        命令帧 Can[0]=cmd/Can[1]=motor/Can[2]=param/Can[3..6]=value 大端；
 *        反馈 ACK/到位帧/位置帧/错误帧（Can[0..6] 全=错误码）
 * @依赖: HAL_FDCAN；协议分发已迁 app/can_cmd.c；本文件无 SPI 直读
 ****************************************************************************/
#ifndef CAN_H
#define CAN_H

#include "main.h"
#include "fdcan.h"

/* ==== 协议常量 ==== */
#define CAN_RX_ID       0x1AA55F42
#define CAN_TX_ID       0x1AA55F43
#define CAN_DATA_LEN    8

/* ==== 位宏 ==== */
#define BIT(n)          (1UL << (n))

/* ==== 命令码（用户协议 v2） ==== */
#define CMD_ABS_POS     0x01   /* 绝对位置移动 */
#define CMD_REL_CW      0x02   /* 相对位置正转 */
#define CMD_REL_CCW     0x03   /* 相对位置反转 */
#define CMD_VELOCITY    0x04   /* 速度模式旋转（Can[6]=0x00正/0xFF反，速度取参数组） */
#define CMD_HOME        0x05   /* 回零（Can[6]=0x00负向/0xFF正向寻零） */
#define CMD_CL_SET      0x06   /* 闭环使能/失能（Can[6]=0x00使能/0xFF失能） */
#define CMD_STOP_ESTOP  0x07   /* 停止/急停（Can[6]=0x00停止/0xFF急停） */
#define CMD_ZERO_POS    0x08   /* 在线清零（长时单向 soak 越限前重建基线） */
#define CMD_READ_POS    0x09   /* 读位置（诊断只读；校准门控豁免，见 require 附录A） */

/* ==== 反馈状态位（到位帧 Can[6] / 位置帧 Can[2] 共用语义） ====
 * bit0 到位：到位帧=本次命令到位判定(软件,超时=0)；位置帧=当前 RAMP_STAT.position_reached
 * bit1 失步：ENC_STATUS.deviation_warn（粘滞，写清前保持）
 * bit2 过温：DRVSTATUS.ot/otpw
 * bit3 驱动错误：GSTAT.drv_err
 * bit4 SPI 通讯异常：任一寄存器读到 0xFFFFFFFF 哨兵值（位置值同时不可信）
 * 依据 .cl/memory/ tmc5160_enc_const + drv/tmc5160.c TMC5160_GetStatusFlags 位定义 */
#define CAN_ST_ARRIVED  0x01U
#define CAN_ST_STALL    0x02U
#define CAN_ST_OTW      0x04U
#define CAN_ST_DRVERR   0x08U
#define CAN_ST_SPIFAIL  0x10U

/* ==== 电机位掩码（协议 Can[1]，接收/反馈统一；bit(i)=电机 i+1） ====
 * 0x01=电机1, 0x02=电机2, 0x03=电机1+2, 0x04=电机3 … 最大 0x1F=5 台 */
#define CAN_MOTOR_BIT(n)    (1U << ((n) - 1U))

/* ==== 错误帧（反馈 ID 复用 0x1AA55F43） ====
 * 特征: Can[0..6] 全部填错误码, Can[7]=checksum
 * 主机判 Can[0..6] 全等即错误帧（正常帧 Can[0]=命令码 0x01~0x09） */

/* 错误码表（Can[0..6]；值避开命令码 0x01~0x08 与电机掩码） */
#define CAN_ERR_UNCALIBRATED   0xE1U   /* 未校准：位置基准丢失，须重新回零 */
#define CAN_ERR_CALIBRATING    0xE2U   /* 正在校准中：回零进行中 */
#define CAN_ERR_INVALID_MASK   0xE3U   /* 非法电机掩码（0 或含 5 位外无效位） */
#define CAN_ERR_INVALID_CMD    0xE4U   /* 未知命令（不在 0x01~0x08） */

/* ==== 类型定义 ==== */
typedef struct {
    int32_t  value;    /* Can[3..6] 大端 32 位（标志类命令仅 Can[6]=0x00/0xFF） */
    uint8_t  cmd;      /* Can[0] */
    uint8_t  motor;    /* Can[1] 位掩码 */
    uint8_t  param;    /* Can[2] 运动参数组 */
    uint8_t  checksum; /* Can[7] */
} CAN_CMD_T;

/* ==== 接口: 驱动原语 ==== */
void    CAN_Init(void);

/* ==== 接口: 反馈组帧发送（分发见 app/can_cmd.h；纯组帧无 SPI） ==== */
/* ACK: Can[0]=命令码回显, Can[1..6]=0 */
uint8_t CAN_SendAckRaw(uint8_t cmd);
/* 到位反馈: Can[0]=cmd, Can[1]=电机(单), Can[2]=参数, Can[3]=到位, Can[4]=过温,
 *          Can[5]=驱动异常, Can[6]=状态位组合（CAN_ST_* 全 5 位，v2.1）
 * 输入 status: CAN_ST_* 组合；Can[3..5] 由 status 的 bit0/bit2/bit3 派生（单源） */
uint8_t CAN_SendArrivalRaw(uint8_t cmd, uint8_t motor, uint8_t param,
                           uint8_t status);
/* 位置反馈（0x09 专用）: Can[0]=cmd, Can[1]=电机(单), Can[2]=状态位组合,
 *          Can[3..6]=XACTUAL int32 大端, 无到位/参数语义 */
uint8_t CAN_SendPosRaw(uint8_t cmd, uint8_t motor, uint8_t status, int32_t pos);
/* 错误帧: Can[0..6] 全=错误码（见上方错误码表） */
uint8_t CAN_SendErrorRaw(uint8_t err_code);

#endif /* CAN_H */
