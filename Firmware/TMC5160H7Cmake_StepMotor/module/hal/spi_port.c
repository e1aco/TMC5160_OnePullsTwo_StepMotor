/*****************************************************************************
 * @文件: spi_port.c
 * @作者: cl
 * @日期: 2026-09-27
 * @版本: v1.0
 * @说明: SPI/GPIO 传输端口 = MCU 内置外设收敛层
 *        SPI3 寄存器级收发 + CS/ENN GPIO + DWT/HAL 延时，
 *        芯片引脚映射表唯一实例（drv 只留器件属性表）
 * @来源: 自 module/drv/tmc5160.c 原语段逐字平移（2026-09-27 方案 B 下沉）
 * @变更点: ① API 按 chip_id 寻址，向上层不暴露 MCU 类型；
 *          ② 引脚表迁入本层，与 drv/tmc5160.c s_chips[] 按 id 一一对应
 * @平台: STM32H750VBT6 (SPI3)
 * @依赖: CubeMX HAL(main.h/spi.h), cmsis_os2(仅 SPI_PortDelayMs 上下文自适应)
 ****************************************************************************/
#include "hal/spi_port.h"
#include "main.h"
#include "spi.h"
#include "cmsis_os2.h"    /* SPI_PortDelayMs 上下文自适应 (osKernelGetState/osDelay) */

/* ==== CS 高电平间隔: tCSH > 2*tCLK+10ns=320ns @6.45MHz (ch04 §4.3), 取 5us 裕量 ==== */
/* 依据 TMC5160A_Datasheet_Rev1.14_ch04_4_spi_interface.md: SPI 时序 */
/* 依据 .cl/memory/config.md: DWT=CPU=SYSCLK=480MHz(D1CPRE=1), 1tick=2.083ns */

#define SPI_PORT_CS_HIGH_US       5U

/* SPI3 属 high-end 实例，TX/RX FIFO 各 16 字节
 * 依据 stm32h750xx.h IS_SPI_HIGHEND_INSTANCE(SPI1/2/3)
 *      + stm32h7xx_hal_spi.h SPI_HIGHEND_FIFO_SIZE=16
 * 5 字节数据报 ≤ FIFO 深度 → 可一次压入 TX 后再取 RX，无需分片 */
#define SPI_PORT_FIFO_DEPTH   16U
/* 轮询收敛兜底：40bit@6.45MHz≈6.2us，HCLK 240MHz 下正常收敛仅数百次迭代，
 * 取 100000 作上界，仅防 SPI 异常时死循环（对应 HAL 的 Timeout 语义） */
#define SPI_PORT_POLL_GUARD   100000UL

/* ==== 端口映射表（引脚/总线硬件描述单源：增减芯片只改本表一行） ====
 * U1: CS=PA11/ENN=PA10；U2: CS=PD3/ENN=PA15；SPI3 共享（双总线预留 spi 字段）
 * 依据 require.md 引脚固定映射表 + main.h CubeMX Pin 宏
 * @注意 增减芯片须同步 drv/tmc5160.c s_chips[]，两表 chip_id 一一对应
 * @结构 前向声明的 SPI_PortBus 在此定义完整体（MCU 类型仅本层可见） */
struct SPI_PortBus {
    uint8_t chip_id;
    GPIO_TypeDef *cs_port;
    uint16_t cs_pin;
    GPIO_TypeDef *enn_port;
    uint16_t enn_pin;
    SPI_TypeDef *spi;
};

#define SPI_PORT_MAP_COUNT  2U

static const SPI_PortBus s_port_map[SPI_PORT_MAP_COUNT] = {
    { .chip_id = 1U, .cs_port = U1_SPI_SCN_GPIO_Port, .cs_pin = U1_SPI_SCN_Pin,
      .enn_port = U1_DRV_ENN_GPIO_Port, .enn_pin = U1_DRV_ENN_Pin, .spi = SPI3 },
    { .chip_id = 2U, .cs_port = U2_SPI_SCN_GPIO_Port, .cs_pin = U2_SPI_SCN_Pin,
      .enn_port = U2_DRV_ENN_GPIO_Port, .enn_pin = U2_DRV_ENN_Pin, .spi = SPI3 },
};

/* ==== 内部函数 ==== */

/**
 * @输入 chip_id: 芯片号
 * @输出 端口句柄指针（未命中返 NULL）
 * @说明 按 id 查端口映射表（表静态连续，线性扫描即可）；
 *       热路径每事务仅查一次且置于计时窗口外，窗口内原语用句柄直通
 */
const SPI_PortBus *SPI_PortOf(uint8_t chip_id)
{
    uint8_t i;

    for (i = 0U; i < SPI_PORT_MAP_COUNT; i++)
    {
        if (s_port_map[i].chip_id == chip_id)
        {
            return &s_port_map[i];
        }
    }
    return (const SPI_PortBus *)0;
}

/**
 * @输入 bus: 端口句柄(SPI_PortOf 产物, 非 NULL);
 *        tx: 发送缓冲(5 字节); rx: 接收缓冲(5 字节); len: 字节数(数据报=5)
 * @输出 SPI_PORT_OK / SPI_PORT_ERR
 * @说明 寄存器级全双工收发，替代 HAL_SPI_TransmitReceive 以去除 HAL 固定开销
 * @注意 SPI 模式/分频/CPOL/CPHA 仍由 CubeMX HAL 配置，本函数只替换收发路径
 * 依据 TMC5160A_Datasheet_Rev1.14_ch04_4_spi_interface.md 第4章:
 *   40-bit 数据报(8bit 地址 + 32bit 数据)，需两报读一寄存器
 * 依据 RM0433(STM32H7) SPI 传输序列(HAL stm32h7xx_hal_spi.c:1410-1647):
 *   SPI_CR2.TSIZE 设数据数 → SPI_CR1.SPE 使能 → SPI_CR1.CSTART 启动
 *   → 轮询 SPI_SR.TXP 写 SPI_TXDR / SPI_SR.RXP 读 SPI_RXDR
 *   → 等 SPI_SR.EOT → 写 SPI_IFCR 清 EOTC/TXTFC → 关 SPI
 */
uint8_t SPI_PortTransferBus(const SPI_PortBus *bus, const uint8_t *tx,
                            uint8_t *rx, uint16_t len)
{
    SPI_TypeDef *spi = bus->spi;
    uint16_t tx_cnt = len;
    uint16_t rx_cnt = len;
    uint16_t tx_idx = 0U;
    uint16_t rx_idx = 0U;
    uint32_t sr;
    uint32_t guard;

    /* 清残留标志(EOT/TXTF/OVR/UDR/MODF)，防上一帧异常污染本帧 */
    spi->IFCR = SPI_IFCR_EOTC | SPI_IFCR_TXTFC | SPI_IFCR_OVRC |
                SPI_IFCR_UDRC | SPI_IFCR_MODFC;

    MODIFY_REG(spi->CR2, SPI_CR2_TSIZE, (uint32_t)len);
    SET_BIT(spi->CR1, SPI_CR1_SPE);
    SET_BIT(spi->CR1, SPI_CR1_CSTART);

    guard = 0U;
    while ((tx_cnt > 0U) || (rx_cnt > 0U))
    {
        if ((tx_cnt > 0U) && (0U != (spi->SR & SPI_SR_TXP)) &&
            (rx_cnt < (tx_cnt + SPI_PORT_FIFO_DEPTH)))
        {
            *(__IO uint8_t *)&spi->TXDR = tx[tx_idx];
            tx_idx++;
            tx_cnt--;
        }
        if ((rx_cnt > 0U) && (0U != (spi->SR & SPI_SR_RXP)))
        {
            rx[rx_idx] = *(__IO uint8_t *)&spi->RXDR;
            rx_idx++;
            rx_cnt--;
        }
        if (++guard > SPI_PORT_POLL_GUARD)
        {
            CLEAR_BIT(spi->CR1, SPI_CR1_SPE);
            return SPI_PORT_ERR;
        }
    }

    /* 等发送/RX 全完成后 EOT */
    guard = 0U;
    while (0U == (spi->SR & SPI_SR_EOT))
    {
        if (++guard > SPI_PORT_POLL_GUARD)
        {
            CLEAR_BIT(spi->CR1, SPI_CR1_SPE);
            return SPI_PORT_ERR;
        }
    }
    sr = spi->SR;
    spi->IFCR = SPI_IFCR_EOTC | SPI_IFCR_TXTFC | SPI_IFCR_OVRC |
                SPI_IFCR_UDRC | SPI_IFCR_MODFC;
    CLEAR_BIT(spi->CR1, SPI_CR1_SPE);

    /* 过速/欠速错误等同 HAL 的 ErrorCode 判定 */
    if (0U != (sr & (SPI_SR_OVR | SPI_SR_UDR)))
    {
        return SPI_PORT_ERR;
    }
    return SPI_PORT_OK;
}

/* ==== 接口: 传输 ==== */

/**
 * @输入 chip_id: 芯片号; tx/rx: 收发缓冲; len: 字节数(数据报=5)
 * @输出 SPI_PORT_OK / SPI_PORT_ERR
 * @说明 id 版兼容包装（冷路径）：解析句柄后转句柄版；热路径请直接用
 *       SPI_PortOf + SPI_PortTransferBus（每事务只查一次表）
 */
uint8_t SPI_PortTransfer(uint8_t chip_id, const uint8_t *tx, uint8_t *rx,
                         uint16_t len)
{
    const SPI_PortBus *bus = SPI_PortOf(chip_id);

    if ((const SPI_PortBus *)0 == bus)
    {
        return SPI_PORT_ERR;
    }
    return SPI_PortTransferBus(bus, tx, rx, len);
}

/* ==== 接口: 片选/使能/延时 ==== */

/**
 * @输入 bus: 端口句柄(非 NULL)
 * @输出 无
 * @说明 拉低片选（BSRR 高 16 位写 1 = 复位引脚，原子、无读-改-写）
 * 依据 RM0433(STM32H7) GPIO: GPIOx_BSRR bits[31:16] 复位
 */
void SPI_PortCsAssertBus(const SPI_PortBus *bus)
{
    bus->cs_port->BSRR = (uint32_t)bus->cs_pin << 16U;
}

/**
 * @输入 bus: 端口句柄(非 NULL)
 * @输出 无
 * @说明 拉高片选（BSRR 低 16 位写 1 = 置位引脚）
 * 依据 RM0433(STM32H7) GPIO: GPIOx_BSRR bits[15:0] 置位
 */
void SPI_PortCsReleaseBus(const SPI_PortBus *bus)
{
    bus->cs_port->BSRR = (uint32_t)bus->cs_pin;
}

/**
 * @输入 chip_id: 芯片号
 * @输出 无
 * @说明 id 版兼容包装（冷路径）：解析句柄后转句柄版；id 未命中则忽略
 */
void SPI_PortCsAssert(uint8_t chip_id)
{
    const SPI_PortBus *bus = SPI_PortOf(chip_id);

    if ((const SPI_PortBus *)0 != bus)
    {
        SPI_PortCsAssertBus(bus);
    }
}

/**
 * @输入 chip_id: 芯片号
 * @输出 无
 * @说明 id 版兼容包装（冷路径）：解析句柄后转句柄版；id 未命中则忽略
 */
void SPI_PortCsRelease(uint8_t chip_id)
{
    const SPI_PortBus *bus = SPI_PortOf(chip_id);

    if ((const SPI_PortBus *)0 != bus)
    {
        SPI_PortCsReleaseBus(bus);
    }
}

/**
 * @输入 chip_id: 芯片号; on: 1=禁用(拉高), 0=使能(拉低)
 * @输出 无
 * @说明 ENN 引脚控制（冷路径）；id 未命中则忽略
 */
void SPI_PortEnnSet(uint8_t chip_id, uint8_t on)
{
    const SPI_PortBus *bus = SPI_PortOf(chip_id);

    if ((const SPI_PortBus *)0 == bus)
    {
        return;
    }
    HAL_GPIO_WritePin(bus->enn_port, bus->enn_pin,
                      (0U != on) ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

/**
 * @输入 ms: 毫秒数
 * @输出 无
 * @说明 毫秒延时（上下文自适应：RTOS 运行中用 osDelay 让出 CPU，
 *       调度器启动前用 HAL_Delay）
 */
void SPI_PortDelayMs(uint32_t ms)
{
    if (osKernelRunning == osKernelGetState())
    {
        osDelay(ms);
    }
    else
    {
        HAL_Delay(ms);
    }
}

/**
 * @输入 us: 微秒数
 * @输出 无
 * @说明 微秒延时（DWT 周期计数，CPU=SYSCLK=480MHz）
 */
void SPI_PortDelayUs(uint32_t us)
{
    uint32_t start, ticks;

    if ((CoreDebug->DEMCR & CoreDebug_DEMCR_TRCENA_Msk) == 0)
    {
        CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
        DWT->CYCCNT = 0;
        DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    }
    else if ((DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk) == 0)
    {
        DWT->CYCCNT = 0;
        DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    }
    ticks = us * (480U);   /* DWT=CPU=480MHz */
    start = DWT->CYCCNT;
    while ((DWT->CYCCNT - start) < ticks)
    {
        __NOP();
    }
}
