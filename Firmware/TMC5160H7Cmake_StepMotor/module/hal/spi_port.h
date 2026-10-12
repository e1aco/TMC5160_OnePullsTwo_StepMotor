/*****************************************************************************
 * @文件: spi_port.h
 * @作者: cl
 * @日期: 2026-09-27
 * @版本: v1.0
 * @说明: SPI/GPIO 传输端口 = MCU 内置外设访问的唯一出口
 *        drv/tmc5160 经此收发；换 MCU 只改本层，drv 器件协议零改动
 * @来源: 2026-09-27 分层重构方案 B（自 drv/tmc5160.c 原语段下沉）
 * @平台: STM32H750VBT6 (SPI3)
 * @依赖: CubeMX HAL(main.h/spi.h), cmsis_os2(仅 DelayMs 上下文自适应)
 ****************************************************************************/
#ifndef SPI_PORT_H
#define SPI_PORT_H

#include <stdint.h>

/* ==== 返回码（数值与 TMC5160_OK/ERR 一致，调用方直接沿用比较） ==== */
#define SPI_PORT_OK   0
#define SPI_PORT_ERR  1

/* ==== 事务级端口句柄（热路径: 每事务解析 1 次, 窗口内原语免查表直通） ==== */
/* 依据 code_style「依赖反转」: 句柄为不透明指针，MCU 类型(GPIO_TypeDef/
 *   SPI_TypeDef)不出本层 — drv 只持有 const SPI_PortBus * */
typedef struct SPI_PortBus SPI_PortBus;

/* ==== 接口: 句柄解析 ==== */

/**
 * @输入 chip_id: 芯片号(见端口映射表)
 * @输出 端口句柄(未命中返 NULL)
 * @说明 按 id 查端口映射表; 热路径(TMC5160_SpiWrite/SpiRead)每事务解析一次
 *       并置于计时窗口外, 窗口内全部原语复用该句柄(09-27 时序回归优化:
 *       原每原语各查一次表 ×3(写)/×7(读) 使 写+2us/读+3.8us)
 */
const SPI_PortBus *SPI_PortOf(uint8_t chip_id);

/* ==== 接口: 传输(句柄版, 热路径) ==== */

/**
 * @输入 bus: SPI_PortOf 返回的句柄(非 NULL); tx/rx: 收发缓冲; len: 字节数(=5)
 * @输出 SPI_PORT_OK / SPI_PORT_ERR
 * @说明 寄存器级全双工收发一次完整事务（不含片选/帧间隔，由调用方控制）
 * @注意 SPI 模式/分频/CPOL/CPHA 仍由 CubeMX HAL 配置，本函数只替换收发路径
 */
uint8_t SPI_PortTransferBus(const SPI_PortBus *bus, const uint8_t *tx,
                            uint8_t *rx, uint16_t len);

/* ==== 接口: 片选(句柄版, 热路径) ==== */

/**
 * @输入 bus: 端口句柄(非 NULL)
 * @输出 无
 * @说明 拉低片选（BSRR 高 16 位写 1 = 复位引脚，原子、无读-改-写）
 * 依据 RM0433(STM32H7) GPIO: GPIOx_BSRR bits[31:16] 复位
 */
void SPI_PortCsAssertBus(const SPI_PortBus *bus);

/**
 * @输入 bus: 端口句柄(非 NULL)
 * @输出 无
 * @说明 拉高片选（BSRR 低 16 位写 1 = 置位引脚）
 * 依据 RM0433(STM32H7) GPIO: GPIOx_BSRR bits[15:0] 置位
 */
void SPI_PortCsReleaseBus(const SPI_PortBus *bus);

/* ==== 接口: 传输(id版, 冷路径兼容) ==== */

/**
 * @输入 chip_id: 芯片号(见端口映射表); tx: 发送缓冲; rx: 接收缓冲;
 *        len: 字节数(数据报=5)
 * @输出 SPI_PORT_OK / SPI_PORT_ERR
 * @说明 寄存器级全双工收发一次完整事务（不含片选/帧间隔，由调用方控制）
 * @注意 SPI 模式/分频/CPOL/CPHA 仍由 CubeMX HAL 配置，本函数只替换收发路径
 */
uint8_t SPI_PortTransfer(uint8_t chip_id, const uint8_t *tx, uint8_t *rx,
                         uint16_t len);

/* ==== 接口: 片选/使能/延时 ==== */

/**
 * @输入 chip_id: 芯片号
 * @输出 无
 * @说明 拉低对应芯片 SPI 片选引脚（寄存器级 BSRR 原子写）；id 未命中则忽略
 */
void SPI_PortCsAssert(uint8_t chip_id);

/**
 * @输入 chip_id: 芯片号
 * @输出 无
 * @说明 拉高对应芯片 SPI 片选引脚（寄存器级 BSRR 原子写）；id 未命中则忽略
 */
void SPI_PortCsRelease(uint8_t chip_id);

/**
 * @输入 chip_id: 芯片号; on: 1=禁用驱动(ENN 拉高), 0=使能驱动(ENN 拉低)
 * @输出 无
 * @说明 ENN 引脚控制；id 未命中则忽略
 */
void SPI_PortEnnSet(uint8_t chip_id, uint8_t on);

/**
 * @输入 ms: 毫秒数
 * @输出 无
 * @说明 毫秒延时（上下文自适应：RTOS 运行中 osDelay 让出 CPU，
 *       调度器启动前 HAL_Delay）
 */
void SPI_PortDelayMs(uint32_t ms);

/**
 * @输入 us: 微秒数
 * @输出 无
 * @说明 微秒延时（DWT 周期计数，CPU=SYSCLK=480MHz 基准）
 */
void SPI_PortDelayUs(uint32_t us);

#endif /* SPI_PORT_H */
