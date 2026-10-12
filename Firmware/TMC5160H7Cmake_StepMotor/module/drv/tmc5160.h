/*****************************************************************************
 * @文件: tmc5160.h
 * @作者: cl
 * @日期: 2026-09-09
 * @版本: v1.0
 * @说明: TMC5160 驱动层统一头 = 原 tmc5160_drv.h + tmc5160_usr.h 文件合并
 *        （用户 2026-09-09 裁决：drv 允许芯片功能完整封装）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一
 * @平台: STM32H750VBT6 (SPI3, 模式3, 6.45Mbit/s; fCLK=15MHz 外部时钟)
 * @依赖: hal/spi_port（SPI 传输/CS/ENN/延时；MCU 类型零引用）
 ****************************************************************************/
#ifndef TMC5160_H
#define TMC5160_H

#include <stdint.h>

/* ==== 芯片数量（单源：与 tmc5160.c s_chips[] 行数一致，增减芯片同步改） ==== */
#define TMC5160_CHIP_COUNT  2U

/* ==== SPI 读写返回码 ==== */
#define TMC5160_OK       0
#define TMC5160_ERR      1

/* ==== 返回码 ==== */
#define TMC5160_SUCCESS  0
#define TMC5160_FAIL     1

/* ==== 运动结果 ==== */
typedef enum {
    TMC5160_MOVE_OK = 0,
    TMC5160_MOVE_TIMEOUT,
    TMC5160_MOVE_DEVIATION,
    TMC5160_MOVE_SPI_ERROR
} TMC5160_MOVE_RESULT_T;

/* ==== 芯片描述：单表 s_chips[]（tmc5160.c）的行类型 ====
 * 器件属性单源；引脚/总线映射在 hal/spi_port.c s_port_map[]（两表 chip_id 对应）；
 * id 与 app 层电机位 bit(i)=电机 i+1 对应(0x01/0x02…)，电机掩码由调用方
 * 按位遍历全表（MOTOR_CTRL_ALL=(1<<COUNT)-1），不进表 */
typedef struct {
    uint8_t id;
    uint8_t closed_loop;
} TMC5160_CHIP_T;

/* ==== 运动参数组 ==== */
typedef struct {
    uint32_t vstart;
    uint32_t vstop;
    uint32_t v1;
    uint32_t a1;
    uint32_t amax;
    uint32_t vmax;
    uint32_t d1;
    uint32_t dmax;
    uint32_t tzerowait;
} TMC5160_PROFILE_T;

#define TMC5160_PROFILE_COUNT  6

/* ==== 常量 ==== */
/* 编码器偏差容限 512µsteps(原256)：24V+传送带实测健康跟随滞后最大263(G6 1442rpm巡航,
 * 到位回落Δ13, 非丢步)，512=1.9×最大健康滞后；真丢步偏移5k~50M量级照样检出。
 * 依据 .cl/memory/config.md encoder_tol=512 + 2026-09-18 G6滞后测量 */
#define TMC5160_ENC_TOLERANCE    512
#define TMC5160_MOVE_TIMEOUT_MS  5000
#define TMC5160_MAX_RETRY        3

/* ==== 回零(StallGuard2 堵转碰硬限位，无编码器) ==== */
#define TMC5160_HOME_DIR_NEGATIVE  0   /* 向负方向（XACTUAL 减小） */
#define TMC5160_HOME_DIR_POSITIVE  1   /* 向正方向（XACTUAL 增大） */
/* 回零速度 2RPS（ch13 §13.4 推荐 1~5RPS）：VMAX=V×2^24/fCLK=102400×2^24/15e6≈114532
 * 依据 .cl/memory/config.md home_vmax=114532 */
#define TMC5160_HOME_VMAX          114532UL

/* ==== 回零 StallGuard2 位定义（drv 原语 + app/homing 快照判位共用） ==== */
/* RAMP_STAT event_stop_sg=bit6(R+WC，写 1 清) 依据 ch06.p044 */
#define TMC5160_RAMP_EVENT_STOP_SG  (1UL << 6)
/* DRVSTATUS SG_RESULT=bits[9:0]（0=最大负载/堵转） 依据 ch06.p056 */
#define TMC5160_SG_RESULT_MASK      0x3FFUL

/* ==== TMC5160 寄存器地址（datasheet 常量；drv 原语 + app/sched 快照采集引用） ==== */
/* 依据 TMC5160A_Datasheet_Rev1.14.ch06.p032.md: 寄存器映射 */
#define REG_GCONF          0x00
#define REG_GSTAT          0x01
#define REG_IHOLD_IRUN     0x10
#define REG_TPOWERDOWN     0x11
#define REG_TPWMTHRS       0x13
#define REG_SHORT_CONF     0x09
#define REG_TCOOLTHRS      0x14
#define REG_COOLCONF       0x6D
#define REG_RAMPMODE       0x20
#define REG_XACTUAL        0x21
#define REG_VACTUAL        0x22
#define REG_VSTART         0x23
#define REG_A1             0x24
#define REG_V1             0x25
#define REG_AMAX           0x26
#define REG_VMAX           0x27
#define REG_DMAX           0x28
#define REG_D1             0x2A
#define REG_VSTOP          0x2B
#define REG_TZEROWAIT      0x2C
#define REG_XTARGET        0x2D
/* 依据 TMC5160A_Datasheet_Rev1.14.ch06.p042.md: SW_MODE/RAMP_STAT */
#define REG_SW_MODE        0x34
#define REG_RAMP_STAT      0x35
#define REG_ENCMODE        0x38
#define REG_X_ENC          0x39
#define REG_ENC_CONST      0x3A
#define REG_ENC_STATUS     0x3B
#define REG_ENC_DEVIATION  0x3D
#define REG_CHOPCONF       0x6C
#define REG_DRV_CONF       0x0A
#define REG_DRVSTATUS      0x6F
#define REG_PWMCONF        0x70

/* ==== 芯片表访问（单源，实例在 tmc5160.c s_chips[]） ==== */
TMC5160_CHIP_T *TMC5160_ChipAt(uint8_t idx);   /* 下标→表项，越界返 NULL */
TMC5160_CHIP_T *TMC5160_Lookup(uint8_t id);    /* id→表项，无匹配返 NULL */

/* ==== 接口: SPI/GPIO 原语 ==== */
void     TMC5160_SelectChip(TMC5160_CHIP_T *chip);
void     TMC5160_DeselectChip(TMC5160_CHIP_T *chip);
void     TMC5160_Enable(TMC5160_CHIP_T *chip);
void     TMC5160_Disable(TMC5160_CHIP_T *chip);
void     TMC5160_DelayMs(uint32_t ms);
uint8_t  TMC5160_SpiWrite(TMC5160_CHIP_T *chip, uint8_t reg_addr, uint32_t data);
uint32_t TMC5160_SpiRead(TMC5160_CHIP_T *chip, uint8_t reg_addr);

/* ==== 接口: 芯片功能封装 ==== */
void    TMC5160_Init(void);
void    TMC5160_EStop(TMC5160_CHIP_T *chip);

void    TMC5160_ApplyProfile(TMC5160_CHIP_T *chip, uint8_t profile_id);
uint32_t TMC5160_GetProfileVmax(uint8_t profile_id);
void    TMC5160_MoveTo(TMC5160_CHIP_T *chip, int32_t target);
void    TMC5160_MoveBy(TMC5160_CHIP_T *chip, int32_t offset);
void    TMC5160_SetVelocity(TMC5160_CHIP_T *chip, int32_t velocity);
void    TMC5160_Stop(TMC5160_CHIP_T *chip);

int32_t TMC5160_GetPosition(TMC5160_CHIP_T *chip);
/* 0x09 位置源选择（can_cmd 取 Can[2]）：0=XACTUAL(指令)/1=X_ENC(编码器) */
#define TMC5160_POS_SRC_XACTUAL  0U
#define TMC5160_POS_SRC_X_ENC    1U
/* 单读位置 + 有效性判定：0=有效/1=SPI 读失败（0xFFFFFFFF 哨兵）；source 见上 */
uint8_t TMC5160_ReadPosition(TMC5160_CHIP_T *chip, uint8_t source, int32_t *pos);
int32_t TMC5160_GetVelocity(TMC5160_CHIP_T *chip);
uint32_t TMC5160_GetRampStat(TMC5160_CHIP_T *chip);
uint32_t TMC5160_GetDrvStatus(TMC5160_CHIP_T *chip);
uint32_t TMC5160_GetGStat(TMC5160_CHIP_T *chip);
uint32_t TMC5160_GetEncStatus(TMC5160_CHIP_T *chip);

uint8_t TMC5160_GetStatusFlags(TMC5160_CHIP_T *chip);
/* 非破坏状态位组合（只读不清任何粘滞位，位定义同 GetStatusFlags：
 * bit0到位/bit1失步/bit2过温/bit3驱动错/bit4 SPI读失败 0xFFFFFFFF 哨兵） */
uint8_t TMC5160_ReadStatusFlags(TMC5160_CHIP_T *chip);
uint8_t TMC5160_GetMotionPhase(TMC5160_CHIP_T *chip);

void    TMC5160_ConfigEncoder(TMC5160_CHIP_T *chip);
int32_t TMC5160_GetEncoderPosition(TMC5160_CHIP_T *chip);
int32_t TMC5160_GetEncoderDeviation(TMC5160_CHIP_T *chip);

/* 在线清零：XACTUAL/X_ENC 写 0 + 软件 offset 归零 + 清 ENC 粘滞位
 * （长时单向 soak 用，上位机须先停稳；回零/急停语义不变） */
void    TMC5160_ZeroPosition(TMC5160_CHIP_T *chip);

TMC5160_MOVE_RESULT_T TMC5160_WaitPosition(TMC5160_CHIP_T *chip, uint32_t timeout_ms);

/* ==== 接口: 回零基础控制（StallGuard2 芯片级原语；状态机/时序在 app/homing） ==== */
void     TMC5160_HomeConfig(TMC5160_CHIP_T *chip);
void     TMC5160_HomeRestore(TMC5160_CHIP_T *chip);
void     TMC5160_HomeClearStall(TMC5160_CHIP_T *chip);
void     TMC5160_HomeZero(TMC5160_CHIP_T *chip);

#endif /* TMC5160_H */
