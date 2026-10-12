/*****************************************************************************
 * @文件: tmc5160.c
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: TMC5160 驱动层 = 芯片功能封装（运动/编码器/状态/回零原语），传输经 hal/spi_port
 *        2026-09-09 用户裁决：tmc5160_usr.c 并入本文件（drv 允许芯片功能完整封装）
 *        2026-09-17 用户裁决：回零拆分为 drv 基础控制（TMC5160_Home* 芯片原语）+
 *        app/homing 编排（状态机/时序/CAN 反馈/闭环目标同步）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一
 * @变更点: ① 片选/使能引脚宏改为 H7 板 U1/U2_SPI_SCN、U1/U2_DRV_ENN；
 *          ② SetMode/DebugTransfer 已删除（2026-09-22 清场：零调用，
 *          H7 板 SD_MODE/SPI_MODE 硬接线为 SPI 模式）；
 *          ③ SPI 速率注释 2.625Mbps → 4Mbit/s（prescaler=16 @64MHz）
 * @平台: STM32H750VBT6 (SPI3)
 * @依赖: hal/spi_port（SPI 传输 / CS / ENN / 延时，MCU 相关全部收敛该层）
 ****************************************************************************/
#include "drv/tmc5160.h"
#include "hal/spi_port.h"   /* SPI_Port*: 传输/CS/ENN/延时端口（MCU 相关唯一入口） */
#include "hal/uart_dbg.h"   /* UART_DBG_Printf: 寻零 SG 跟踪诊断 */
#include "tim_test.h"   /* TEST_TIM_* 探针宏: 宏版实测/生产版空宏零开销 (probe.md) */

/* ==== CS 高电平间隔: tCSH > 2*tCLK+10ns=320ns @6.45MHz (ch04 §4.3), 取 5us 裕量 ==== */
/* 依据 TMC5160A_Datasheet_Rev1.14_ch04_4_spi_interface.md: SPI 时序 */
/* 依据 .cl/memory/config.md: DWT=CPU=SYSCLK=480MHz(D1CPRE=1), 1tick=2.083ns */

#define TMC5160_CS_HIGH_US       5U

/* 寄存器地址已移至 tmc5160.h（drv 原语 + app/sched 快照采集共用） */

/* ==== 芯片单表（器件属性单源：增减芯片改本表 + hal/spi_port.c s_port_map[]） ====
 * @注意 引脚/总线映射在 hal/spi_port.c s_port_map[]，两表 chip_id 一一对应 */
static TMC5160_CHIP_T s_chips[TMC5160_CHIP_COUNT] = {
    { .id = 1U, .closed_loop = 0U },
    { .id = 2U, .closed_loop = 0U },
};

/* 下标→表项内用：指针差等价循环比对（表静态连续，无越界指针运算） */
static uint8_t S_Idx(const TMC5160_CHIP_T *chip)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        if (&s_chips[i] == chip)
        {
            return i;
        }
    }
    return TMC5160_CHIP_COUNT;
}

TMC5160_CHIP_T *TMC5160_ChipAt(uint8_t idx)
{
    if (idx >= TMC5160_CHIP_COUNT)
    {
        return (void *)0;
    }
    return &s_chips[idx];
}

TMC5160_CHIP_T *TMC5160_Lookup(uint8_t id)
{
    uint8_t i;

    for (i = 0U; i < TMC5160_CHIP_COUNT; i++)
    {
        if (s_chips[i].id == id)
        {
            return &s_chips[i];
        }
    }
    return (void *)0;
}

/* ==== 全局实例 ==== */

/* 编码器零位偏移：上电时 X_ENC 的初始值，后续读数减去此值归零 */
static int32_t s_enc_offset[TMC5160_CHIP_COUNT];

/* 急停锁存（CAN 0x0A）：1=功率级已关断，后续运动命令须先重使能 */
static uint8_t s_estop[TMC5160_CHIP_COUNT];

/* 加速度优化(2026-09-18, VMAX冻结): 各组AMAX/DMAX/D1=干净最大×降额, DMAX=AMAX满足ch22(DMAX≥AMAX)
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md:
 *   AMAX[µsteps/ta²] ta²=2^41/fCLK²(fCLK=15MHz) → 28000≈2.87M µsteps/s²=56rev/s²,
 *   39200≈4.01M µsteps/s²=78rev/s²
 * 方法: AMAX自顶向下探(40000=已实测可写上限, 更高须回读验证未做), 每候选rel往返正反各2趟,
 *   判据=终态bit0=1且失步位0; G1~G5取40000×0.7=28000, G6取40000×0.98=39200(极限语义)
 * 实测: G1/G2/G3/G4/G5@40000各4腿全干净, G6@30000/@40000各4腿全干净(2026-09-18) */
static const TMC5160_PROFILE_T s_profiles[TMC5160_PROFILE_COUNT] = {
    {0, 10, 0, 0, 28000, 5000, 28000, 28000, 10},
    {0, 10, 0, 0, 28000, 20000, 28000, 28000, 10},
    {0, 10, 0, 0, 28000, 50000, 28000, 28000, 10},
    {0, 10, 0, 0, 28000, 100000, 28000, 28000, 10},
    /* 组5 生产高速档: VMAX=572303=10rev/s=600rpm(需求锁上限)@fCLK=15MHz,
     * AMAX/DMAX=28000(加速2.87M µsteps/s²=56rev/s², 0.18s达速; 40000试探干净×0.7)
     * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md:
     *   VMAX[µsteps/t] t=2^24/fCLK → 10×51200×2^24/15e6=572303(上限2^23-512=8388608 OK)
     *   AMAX[µsteps/ta²] ta²=2^41/fCLK² → 20000×15e6²/2^41≈2.05M µsteps/s²
     * 依据 .cl/memory/config.md: 24V+传送带实测失速边界1200<stall≤1500rpm(U2先丢),
     *   600rpm双电机15s零滑动PASS(2026-09-18), 裕量2倍; 原50rps档已删(超需求锁×5且必失速)
     * 依据 .cl/memory/config.md stm32_dwt_cyccnt_clk: fCLK=TIM4 15MHz(TIM4CLK=240MHz/16) */
    {0, 10, 0, 0, 28000, 572303, 28000, 28000, 10},
    /* 组6 极限运行档 VMAX=581841=610rpm（2026-09-29 重载二分认证值）@fCLK=15MHz,
     * AMAX/DMAX=39200沿用(相对组5仅降速, 变量唯一)
     * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md:
     *   VMAX[µsteps/t], t=2^24/fCLK(fCLK=15MHz) → raw = rev/s×51200×2^24/15e6
     *   (实测校准 ≈953.84·rpm⁻¹): 600rpm→572303, 610rpm→581841
     * 依据 .cl/memory/config.md + .cl/capture/bisect_log.csv（2026-09-29 重载重物压带双机二分,
     *   判据=速度模式编码器滞后 max|XACTUAL-X_ENC|, 熔断>640 或 ENC_STATUS.deviation_warn）:
     *   600rpm 10s PASS(lag212); 618.75rpm 10s PASS(lag199); 637.5rpm 4.8s失步;
     *   675/750/900rpm 均<1s失步; 610rpm 60s 认证 PASS(maxlag216, es/gs=0, 无OT, BKP0R不变)
     * 极限运行专用·生产禁用·有人值守·每次使用后检查失步位, 丢步须断电重建零点 */
    {0, 10, 0, 0, 39200, 581841, 39200, 39200, 10},
};
/* ==== 内部函数 ==== */

/**
 * @输入 reg_value: 读取到的寄存器值
 * @输出 0=有效, 1=无效
 * @说明 0xFFFFFFFF 视为 SPI 读失败哨兵值
 */
static uint8_t S_RegValid(uint32_t reg_value)
{
    if (0xFFFFFFFF == reg_value)
    {
        return 1;
    }
    return 0;
}

/* ==== SPI/GPIO 原语（实现在 hal/spi_port.c，此处为薄封装） ==== */

/**
 * @输入 chip: 芯片指针（表项，NULL 直接返回）
 * @输出 无
 * @说明 拉低对应芯片 SPI 片选引脚（经 hal/spi_port，BSRR 原子写）
 */
void TMC5160_SelectChip(TMC5160_CHIP_T *chip)
{
    if ((void *)0 == chip)
    {
        return;
    }
    SPI_PortCsAssert(chip->id);
}

/**
 * @输入 chip: 芯片指针（表项，NULL 直接返回）
 * @输出 无
 * @说明 拉高对应芯片 SPI 片选引脚（经 hal/spi_port，BSRR 原子写）
 */
void TMC5160_DeselectChip(TMC5160_CHIP_T *chip)
{
    if ((void *)0 == chip)
    {
        return;
    }
    SPI_PortCsRelease(chip->id);
}

/**
 * @输入 chip: 芯片指针（表项，NULL 直接返回）
 * @输出 无
 * @说明 拉低 ENN 引脚使能电机驱动（经 hal/spi_port）
 */
void TMC5160_Enable(TMC5160_CHIP_T *chip)
{
    if ((void *)0 == chip)
    {
        return;
    }
    SPI_PortEnnSet(chip->id, 0U);
}

/**
 * @输入 chip: 芯片指针（表项，NULL 直接返回）
 * @输出 无
 * @说明 拉高 ENN 引脚禁用电机驱动（经 hal/spi_port）
 */
void TMC5160_Disable(TMC5160_CHIP_T *chip)
{
    if ((void *)0 == chip)
    {
        return;
    }
    SPI_PortEnnSet(chip->id, 1U);
}

/**
 * @输入 ms: 毫秒数
 * @输出 无
 * @说明 毫秒延时（上下文自适应，实现在 hal/spi_port.c）
 */
void TMC5160_DelayMs(uint32_t ms)
{
    SPI_PortDelayMs(ms);
}

/**
 * @输入 chip: 芯片指针（表项）; reg_addr: 寄存器地址(7bit); data: 32 位数据
 * @输出 TMC5160_OK / TMC5160_ERR
 * @说明 通过 SPI 写入 TMC5160 寄存器
 *   SPI 数据报(40-bit, 5字节): Byte0=bit7(1写)+bit6-0地址, Byte1-4=数据
 * @注意 收发经 hal/spi_port（寄存器级），总线配置仍由 CubeMX HAL 提供
 * 依据 TMC5160A_Datasheet_Rev1.14_ch04_4_spi_interface.md 第4章:
 *   SPI DATAGRAM 格式; 写访问地址加 0x80 (ch06.p031)
 */
uint8_t TMC5160_SpiWrite(TMC5160_CHIP_T *chip, uint8_t reg_addr, uint32_t data)
{
    /* 收发缓冲为局部变量：多任务并发调用时禁止共享 static 缓冲（RTOS 移植 Q4） */
    uint8_t tx_buf[5];
    uint8_t rx_buf[5];
    uint8_t ret;
    const SPI_PortBus *bus;

    if ((void *)0 == chip)
    {
        return TMC5160_ERR;
    }

    /* 事务级句柄解析: 置于计时窗口外, 窗口内 3 原语免查表直通(09-27 优化) */
    bus = SPI_PortOf(chip->id);
    if ((const SPI_PortBus *)0 == bus)
    {
        return TMC5160_ERR;
    }

    tx_buf[0] = reg_addr | 0x80;
    tx_buf[1] = (data >> 24) & 0xFF;
    tx_buf[2] = (data >> 16) & 0xFF;
    tx_buf[3] = (data >> 8) & 0xFF;
    tx_buf[4] = data & 0xFF;

    TEST_TIM_Start(0);   /* tag0=写事务全窗(片选+收发) */
    SPI_PortCsAssertBus(bus);
    ret = SPI_PortTransferBus(bus, tx_buf, rx_buf, 5U);
    SPI_PortCsReleaseBus(bus);
    TEST_TIM_Stop(0);

    /* CS 拉高间隔: 保证背靠背写帧之间 tCSH 达标
     * 依据 TMC5160A_Datasheet_Rev1.14_ch04_4_spi_interface.md §4.3 表:
     *   tCSH > 2*tCLK + 10ns; SCK=6.45MHz → tCLK=155ns → 2*155+10=320ns 最低要求,
     *   取 5us ≈ 15x 裕量(与读两报间隔同值) */
    SPI_PortDelayUs(TMC5160_CS_HIGH_US);

    return ret;
}

/**
 * @输入 chip: 芯片指针（表项）; reg_addr: 寄存器地址(7bit)
 * @输出 32 位寄存器值，失败返回 0xFFFFFFFF
 * @说明 通过 SPI 读取 TMC5160 寄存器，需两报：第一报丢弃旧数据，第二报取新数据
 * @注意 两报之间 CS 拉高需满足 tCSH > 2*tCLK+10ns (datasheet ch04 §4.3 表,
 *       SCK=4MHz → 510ns), 取 10us ≈ 20x 裕量
 * 依据 TMC5160A_Datasheet_Rev1.14_ch04_4_spi_interface.md 第4章:
 *   SPI DATAGRAM 格式
 */
uint32_t TMC5160_SpiRead(TMC5160_CHIP_T *chip, uint8_t reg_addr)
{
    /* 收发缓冲为局部变量：多任务并发调用时禁止共享 static 缓冲（RTOS 移植 Q4） */
    uint8_t tx_buf[5];
    uint8_t rx_buf[5];
    uint8_t ret;
    const SPI_PortBus *bus;

    if ((void *)0 == chip)
    {
        return 0xFFFFFFFF;
    }

    /* 事务级句柄解析: 置于计时窗口外, 窗口内 7 原语免查表直通(09-27 优化) */
    bus = SPI_PortOf(chip->id);
    if ((const SPI_PortBus *)0 == bus)
    {
        return 0xFFFFFFFF;
    }

    tx_buf[0] = reg_addr & 0x7F;
    tx_buf[1] = 0x00;
    tx_buf[2] = 0x00;
    tx_buf[3] = 0x00;
    tx_buf[4] = 0x00;

    /* 第一报：丢弃旧数据 */
    TEST_TIM_Start(1);   /* tag1=读双报总窗(含 10us 帧间隔) */
    SPI_PortCsAssertBus(bus);
    ret = SPI_PortTransferBus(bus, tx_buf, rx_buf, 5U);
    SPI_PortCsReleaseBus(bus);
    if (TMC5160_OK != ret)
    {
        return 0xFFFFFFFF;
    }

    /* CS 高电平间隔: 5us (tCSH > 2*tCLK+10ns=320ns @6.45MHz, 取 ~15× 裕量) */
    SPI_PortDelayUs(TMC5160_CS_HIGH_US);

    /* 第二报：获取本次数据 */
    SPI_PortCsAssertBus(bus);
    ret = SPI_PortTransferBus(bus, tx_buf, rx_buf, 5U);
    SPI_PortCsReleaseBus(bus);
    TEST_TIM_Stop(1);
    if (TMC5160_OK != ret)
    {
        return 0xFFFFFFFF;
    }

    return ((uint32_t)rx_buf[1] << 24) |
           ((uint32_t)rx_buf[2] << 16) |
           ((uint32_t)rx_buf[3] << 8) |
           (uint32_t)rx_buf[4];
}

/* ==== 芯片功能封装 ==== */

/* 急停恢复：锁存有效时重使能（使能后延时等校准，时序同 init 使能段） */
static void S_RecoverIfEStop(TMC5160_CHIP_T *chip)
{
    uint8_t idx;

    if ((void *)0 == chip)
    {
        return;
    }
    idx = S_Idx(chip);
    if (idx >= TMC5160_CHIP_COUNT)
    {
        return;
    }
    if (0U != s_estop[idx])
    {
        s_estop[idx] = 0;
        TMC5160_Enable(chip);
        TMC5160_DelayMs(10);
    }
}

/**
 * @输入 chip: 芯片指针（表项，NULL/野指针直接返回）
 * @输出 无
 * @说明 急停：拉高 ENN 引脚关断功率级，轴自由停车（不锁轴）；
 *        置锁存，后续运动命令自动重使能；位置已丢失须重回零
 */
void TMC5160_EStop(TMC5160_CHIP_T *chip)
{
    uint8_t idx;

    if ((void *)0 == chip)
    {
        return;
    }
    idx = S_Idx(chip);
    if (idx >= TMC5160_CHIP_COUNT)
    {
        return;
    }
    TMC5160_Disable(chip);
    s_estop[idx] = 1;
}

/**
 * @输入 无
 * @输出 无
 * @说明 初始化两片 TMC5160：默认配置/清错/基础寄存器/编码器/电流/使能
 * @注意 模式固定 SPI 模式（H7 板模式引脚硬接线），closed_loop 默认关
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p032.md: GCONF/COOLCONF/TPWMTHRS  2026-08-24
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p048.md: CHOPCONF  2026-08-24
 * 依据 TMC5160A_Datasheet_Rev1.14.ch03.p017.md: DRV_CONF.DRVSTRENGTH  2026-08-24
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p035.md: SHORT_CONF  2026-09-10
 * 依据 TMC5160A_Datasheet_Rev1.14.ch18_18_sine_wave_look.md: PWMCONF  2026-08-24
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p038.md: IHOLD_IRUN/TPOWERDOWN/TCOOLTHRS  2026-09-10
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p045.md: ENCMODE/ENC_CONST  2026-09-01
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p006.md: 使能时序(ENN)  2026-08-24
 */
void TMC5160_Init(void)
{
    uint8_t i;
    uint32_t gstat;

    /* 等待 TMC5160 上电稳定后再操作 SPI */
    TMC5160_DelayMs(50);

    for (i = 0; i < TMC5160_CHIP_COUNT; i++)
    {
        TMC5160_CHIP_T *chip = TMC5160_ChipAt(i);

        if ((void *)0 == chip)
        {
            continue;
        }
        /* 固定默认配置（RAM 常驻，上电初始化写入；id/引脚来自单表） */
        chip->closed_loop = 0;

        /* 上电保持 ENN 高(禁用)，写配置后再使能，避免瞬时过流 */

        /* 清除 Power-on 残留错误，同时验证 SPI 通信 */
        TMC5160_SpiWrite(chip, REG_GSTAT, 0x07);
        TMC5160_DelayMs(1);
        gstat = TMC5160_SpiRead(chip, REG_GSTAT);
        if (0xFFFFFFFF == gstat)
        {
            /* SPI 通信异常，重试一次 */
            TMC5160_DelayMs(10);
            TMC5160_SpiWrite(chip, REG_GSTAT, 0x07);
            TMC5160_DelayMs(1);
            gstat = TMC5160_SpiRead(chip, REG_GSTAT);
        }

        /* 清除编码器粘滞位（MCU 复位/J-Link 刷机不复位 TMC5160，
         * 残留 deviation_warn/n_event 会污染下次运动判据；
         * 依据 ch06.p045：写 1 清，警告仍在则清不掉） */
        TMC5160_SpiWrite(chip, REG_ENC_STATUS, 0x03);

        /* GCONF: en_pwm_mode=0, 全程 SpreadCycle */
        TMC5160_SpiWrite(chip, REG_GCONF, 0x00);

        /* CHOPCONF: TOFF=5, TBL=54clk, MRES=256微步 */
        TMC5160_SpiWrite(chip, REG_CHOPCONF, 0x000181C5);

        /* 驱动配置: DRVSTRENGTH=weak(00)（AOD4126 Qgd=10nC） */
        TMC5160_SpiWrite(chip, REG_DRV_CONF, 0x00000400);

        /* SHORT_CONF: 短路检测灵敏度最低 */
        TMC5160_SpiWrite(chip, REG_SHORT_CONF, 0x0007030F);

        /* PWMCONF: 斩波频率 43.9kHz + PWM 自动调校 */
        TMC5160_SpiWrite(chip, REG_PWMCONF, 0xC40D001E);

        /* 编码器配置 */
        TMC5160_ConfigEncoder(chip);

        /* 电机电流: IHOLDDELAY=8 / IRUN=29(4.31A RMS) / IHOLD=8(1.29A)
         * 依据 .cl/memory/config.md tmc5160_irun=29/tmc5160_ihold=8 推导：
         * 要求运行 4.3A RMS → (CS+1)=4.3×32×√2/6.5=29.9 → CS=29（超电机额定4A，盯温升）
         * → (29+1)/32×6.5/√2=4.31A；要求保持适度提高 → IHOLD=8
         * → (8+1)/32×6.5/√2=1.29A（6=1.01A，3 锁不住） */
        TMC5160_SpiWrite(chip, REG_IHOLD_IRUN, (8 << 16) | (29 << 8) | 8);

        /* 静止降流延迟: 40 → 约 699ms */
        TMC5160_SpiWrite(chip, REG_TPOWERDOWN, 40);

        /* CoolStep 关闭 */
        TMC5160_SpiWrite(chip, REG_COOLCONF, 0x0000);

        /* CoolStep 速度窗口: 0 = 关闭 */
        TMC5160_SpiWrite(chip, REG_TCOOLTHRS, 0);

        /* SW_MODE 标准值 0：清复位残留 sg_stop（回零结束 HomeRestore 亦写 0） */
        TMC5160_SpiWrite(chip, REG_SW_MODE, 0);

        /* TPWMTHRS=0（本模式不参与斩波切换） */
        TMC5160_SpiWrite(chip, REG_TPWMTHRS, 0);

        /* 使能时序: 配置写完后延时稳定, 再拉低 ENN 引脚使能, 随后延时等待校准
         * 避免上电瞬时过流(当前/斩波参数已就绪时才导通功率级) */
        TMC5160_DelayMs(10);
        TMC5160_Enable(chip);
        TMC5160_DelayMs(10);
    }

    /* 等待斩波校准稳定 */
    TMC5160_DelayMs(100);

    /* 记录编码器上电初始值，后续读数减去此偏移归零 */
    for (i = 0; i < TMC5160_CHIP_COUNT; i++)
    {
        s_enc_offset[i] = (int32_t)TMC5160_SpiRead(TMC5160_ChipAt(i), REG_X_ENC);
    }
}

/**
 * @输入 chip: 芯片指针; profile_id: 运动参数组 ID(1~5)
 * @输出 无
 * @说明 按预配置运动参数组设置斜坡寄存器
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md:
 *   VSTART/A1/V1/AMAX/VMAX/DMAX/D1/VSTOP/TZEROWAIT
 */
void TMC5160_ApplyProfile(TMC5160_CHIP_T *chip, uint8_t profile_id)
{
    const TMC5160_PROFILE_T *p;

    if (0 == profile_id || TMC5160_PROFILE_COUNT < profile_id)
    {
        profile_id = 1;
    }
    p = &s_profiles[profile_id - 1];

    TMC5160_SpiWrite(chip, REG_VSTART, p->vstart);
    TMC5160_SpiWrite(chip, REG_VSTOP, p->vstop);
    TMC5160_SpiWrite(chip, REG_V1, p->v1);
    TMC5160_SpiWrite(chip, REG_A1, p->a1);
    TMC5160_SpiWrite(chip, REG_AMAX, p->amax);
    TMC5160_SpiWrite(chip, REG_VMAX, p->vmax);
    TMC5160_SpiWrite(chip, REG_DMAX, p->dmax);
    TMC5160_SpiWrite(chip, REG_D1, p->d1);
    TMC5160_SpiWrite(chip, REG_TZEROWAIT, p->tzerowait);
}

/**
 * @输入 profile_id: 运动参数组 ID(1~6，非法钳为 1，同 ApplyProfile)
 * @输出 该组 VMAX 寄存器值（速度模式 0x04 取速用，无 SPI）
 */
uint32_t TMC5160_GetProfileVmax(uint8_t profile_id)
{
    if ((0U == profile_id) || (TMC5160_PROFILE_COUNT < profile_id))
    {
        profile_id = 1U;
    }
    return s_profiles[profile_id - 1U].vmax;
}

/* ==== 运动控制 ==== */

/**
 * @输入 chip: 芯片指针; target: 目标绝对位置
 * @输出 无
 * @说明 绝对定位，RAMPMODE=0 位置模式
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md:
 *   RAMPMODE/XTARGET
 */
void TMC5160_MoveTo(TMC5160_CHIP_T *chip, int32_t target)
{
    S_RecoverIfEStop(chip);
    TMC5160_SpiWrite(chip, REG_RAMPMODE, 0);
    TMC5160_SpiWrite(chip, REG_XTARGET, (uint32_t)target);
}

/**
 * @输入 chip: 芯片指针; offset: 相对偏移量(+正转, -反转)
 * @输出 无
 * @说明 从当前位置运动指定偏移量
 */
void TMC5160_MoveBy(TMC5160_CHIP_T *chip, int32_t offset)
{
    int32_t current = TMC5160_GetPosition(chip);

    TMC5160_MoveTo(chip, current + offset);
}

/**
 * @输入 chip: 芯片指针; velocity: 目标速度(+正转, -反转)
 * @输出 无
 * @说明 速度模式持续旋转
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md:
 *   RAMPMODE 速度模式
 */
void TMC5160_SetVelocity(TMC5160_CHIP_T *chip, int32_t velocity)
{
    S_RecoverIfEStop(chip);
    if (0 <= velocity)
    {
        TMC5160_SpiWrite(chip, REG_RAMPMODE, 1);
        TMC5160_SpiWrite(chip, REG_VMAX, (uint32_t)velocity);
    }
    else
    {
        TMC5160_SpiWrite(chip, REG_RAMPMODE, 2);
        TMC5160_SpiWrite(chip, REG_VMAX, (uint32_t)(-velocity));
    }
}

/**
 * @输入 chip: 芯片指针
 * @输出 无
 * @说明 立即停止，切回定位模式保持锁轴
 */
void TMC5160_Stop(TMC5160_CHIP_T *chip)
{
    TMC5160_SpiWrite(chip, REG_VMAX, 0);
    TMC5160_SpiWrite(chip, REG_RAMPMODE, 0);
}

/* ==== 位置读取 ==== */

/**
 * @输入 chip: 芯片指针
 * @输出 int32_t: 当前位置(XACTUAL)
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md: XACTUAL
 */
int32_t TMC5160_GetPosition(TMC5160_CHIP_T *chip)
{
    return (int32_t)TMC5160_SpiRead(chip, REG_XACTUAL);
}

/**
 * @输入 chip: 芯片指针; source: 位置源(0=XACTUAL 指令/1=X_ENC 编码器);
 *        pos: 输出位置
 * @输出 0=读取有效, 1=SPI 读失败(0xFFFFFFFF 哨兵, *pos 不可信)
 * @说明 单次 SPI 读位置 + 有效性判定（0x09 位置帧专用，避免多读寄存器）
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md: XACTUAL
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p045.md: X_ENC (R 0x39 编码器计数)
 */
uint8_t TMC5160_ReadPosition(TMC5160_CHIP_T *chip, uint8_t source, int32_t *pos)
{
    uint32_t raw;
    uint8_t reg;

    if ((void *)0 == pos)
    {
        return 1U;
    }
    reg = (TMC5160_POS_SRC_X_ENC == source) ? REG_X_ENC : REG_XACTUAL;
    raw = TMC5160_SpiRead(chip, reg);
    if (0U != S_RegValid(raw))
    {
        return 1U;
    }
    *pos = (int32_t)raw;
    return 0U;
}

/**
 * @输入 chip: 芯片指针
 * @输出 int32_t: VACTUAL 当前实际速度（有符号，负=反转）
 * @依据 TMC5160A_Datasheet_Rev1.14.ch05.p038.md:
 *   R 0x22 20 VACTUAL（斜坡发生器实时速度）
 */
int32_t TMC5160_GetVelocity(TMC5160_CHIP_T *chip)
{
    return (int32_t)TMC5160_SpiRead(chip, 0x22);
}

/**
 * @输入 chip: 芯片指针
 * @输出 uint32_t: RAMP_STAT 寄存器值
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md: RAMP_STAT
 */
uint32_t TMC5160_GetRampStat(TMC5160_CHIP_T *chip)
{
    return TMC5160_SpiRead(chip, REG_RAMP_STAT);
}

/**
 * @输入 chip: 芯片指针
 * @输出 uint32_t: DRVSTATUS 寄存器值
 * 依据 TMC5160A_Datasheet_Rev1.14_ch11_11_diagnostics_and_protection.md
 */
uint32_t TMC5160_GetDrvStatus(TMC5160_CHIP_T *chip)
{
    return TMC5160_SpiRead(chip, REG_DRVSTATUS);
}

/**
 * @输入 chip: 芯片指针
 * @输出 uint32_t: GSTAT 寄存器值（reset/drv_err/uv_cp）
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p033.md: GSTAT
 */
uint32_t TMC5160_GetGStat(TMC5160_CHIP_T *chip)
{
    return TMC5160_SpiRead(chip, REG_GSTAT);
}

/**
 * @输入 chip: 芯片指针
 * @输出 uint32_t: ENC_STATUS 寄存器值（deviation_warn 等）
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p045.md: ENC_STATUS
 */
uint32_t TMC5160_GetEncStatus(TMC5160_CHIP_T *chip)
{
    return TMC5160_SpiRead(chip, REG_ENC_STATUS);
}

/* ==== 状态标志 ==== */

/**
 * @输入 chip: 芯片指针
 * @输出 uint8_t 状态标志位
 *   bit0=到位, bit1=失步, bit2=过温, bit3=驱动错误, bit4=SPI通讯异常
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md: RAMP_STAT
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p045.md: ENC_STATUS
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p033.md: GSTAT/DRVSTATUS
 */
uint8_t TMC5160_GetStatusFlags(TMC5160_CHIP_T *chip)
{
    uint32_t ramp_stat, drv_status, gstat, enc_stat;
    uint8_t flags = 0;

    ramp_stat = TMC5160_SpiRead(chip, REG_RAMP_STAT);
    drv_status = TMC5160_SpiRead(chip, REG_DRVSTATUS);
    gstat = TMC5160_SpiRead(chip, REG_GSTAT);
    enc_stat = TMC5160_SpiRead(chip, REG_ENC_STATUS);

    /* SPI 通讯异常检测 */
    if (S_RegValid(ramp_stat) ||
        S_RegValid(drv_status) ||
        S_RegValid(gstat))
    {
        flags |= 0x10;
        return flags;
    }

    /* bit0: 到位 - RAMP_STAT.bit9 */
    if (ramp_stat & (1UL << 9))
    {
        flags |= 0x01;
        TMC5160_SpiWrite(chip, REG_RAMP_STAT, (1UL << 9));
    }
    /* bit1: 失步 - ENC_STATUS.bit1 (deviation_warn) */
    if (enc_stat & (1UL << 1))
    {
        flags |= 0x02;
        TMC5160_SpiWrite(chip, REG_ENC_STATUS, (1UL << 1));
    }
    /* bit2: 过温 - DRVSTATUS.bit26/25 */
    if (drv_status & (3UL << 25))
    {
        flags |= 0x04;
    }
    /* bit3: 驱动错误 - GSTAT.bit1 (drv_err) */
    if (gstat & 0x02)
    {
        flags |= 0x08;
        TMC5160_SpiWrite(chip, REG_GSTAT, 0x02);
    }

    return flags;
}

/**
 * @输入 chip: 芯片指针
 * @输出 uint8_t 状态位组合（非破坏）
 *   bit0=到位, bit1=失步, bit2=过温, bit3=驱动错误, bit4=SPI通讯异常
 * @说明 与 TMC5160_GetStatusFlags 位定义一致，但**只读不写清**——不清
 *       RAMP_STAT.position_reached / ENC_STATUS.deviation_warn / GSTAT.drv_err
 *       粘滞位（到位帧/位置帧上报用；写清版见 GetStatusFlags）
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md: RAMP_STAT.bit9
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p045.md: ENC_STATUS.bit1 deviation_warn
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p033.md: GSTAT.bit1 / DRVSTATUS.bit26|25
 */
uint8_t TMC5160_ReadStatusFlags(TMC5160_CHIP_T *chip)
{
    uint32_t ramp_stat;
    uint32_t drv_status;
    uint32_t gstat;
    uint32_t enc_stat;
    uint8_t flags = 0;

    if ((void *)0 == chip)
    {
        return 0;
    }

    ramp_stat  = TMC5160_SpiRead(chip, REG_RAMP_STAT);
    drv_status = TMC5160_SpiRead(chip, REG_DRVSTATUS);
    gstat      = TMC5160_SpiRead(chip, REG_GSTAT);
    enc_stat   = TMC5160_SpiRead(chip, REG_ENC_STATUS);

    /* bit4: SPI 通讯异常（任一路读到 0xFFFFFFFF 哨兵值 → 状态与位置均不可信） */
    if ((0U != S_RegValid(ramp_stat)) || (0U != S_RegValid(drv_status)) ||
        (0U != S_RegValid(gstat)) || (0U != S_RegValid(enc_stat)))
    {
        flags |= 0x10;
    }

    if (0UL != (ramp_stat & (1UL << 9)))          /* bit0 到位 */
    {
        flags |= 0x01;
    }
    if (0UL != (enc_stat & (1UL << 1)))           /* bit1 失步 */
    {
        flags |= 0x02;
    }
    if (0UL != (drv_status & (3UL << 25)))        /* bit2 过温 */
    {
        flags |= 0x04;
    }
    if (0UL != (gstat & 0x02UL))                  /* bit3 驱动错误 */
    {
        flags |= 0x08;
    }

    return flags;
}

/**
 * @输入 chip: 芯片指针
 * @输出 uint8_t: 运动阶段标志
 *   bit0=加速, bit1=匀速, bit2=减速, bit3=归零等待, bit4=静止锁轴
 * 依据 TMC5160A_Datasheet_Rev1.14.ch12_12_ramp_generator.md: RAMP_STAT
 */
uint8_t TMC5160_GetMotionPhase(TMC5160_CHIP_T *chip)
{
    uint32_t ramp_stat;
    uint8_t phase = 0;

    ramp_stat = TMC5160_SpiRead(chip, REG_RAMP_STAT);

    if (ramp_stat & (1UL << 5))
    {
        phase |= 0x01;    /* 加速 */
    }
    if (ramp_stat & (1UL << 6))
    {
        phase |= 0x02;    /* 匀速 */
    }
    if (ramp_stat & (1UL << 7))
    {
        phase |= 0x04;    /* 减速 */
    }
    if (ramp_stat & (1UL << 10))
    {
        phase |= 0x08;    /* 归零等待 */
    }
    if (ramp_stat & (1UL << 0))
    {
        phase |= 0x10;    /* 静止锁轴 */
    }

    return phase;
}

/* ==== 编码器 ==== */

/**
 * @输入 chip: 芯片指针
 * @输出 无
 * @说明 配置编码器接口
 * 依据 TMC5160A_Datasheet_Rev1.14.ch20_20_abn_incremental_encoder.md:
 *   ENCMODE/ENC_CONST/ENC_DEVIATION
 */
void TMC5160_ConfigEncoder(TMC5160_CHIP_T *chip)
{
    TMC5160_SpiWrite(chip, REG_ENCMODE, 0x00);
    TMC5160_SpiWrite(chip, REG_ENC_CONST, 0xFFF33333);
    TMC5160_SpiWrite(chip, REG_ENC_DEVIATION, TMC5160_ENC_TOLERANCE);
}

/**
 * @输入 chip: 芯片指针
 * @输出 int32_t: 编码器当前位姿(X_ENC 归零后)
 */
int32_t TMC5160_GetEncoderPosition(TMC5160_CHIP_T *chip)
{
    int32_t raw;
    uint8_t idx;

    if ((void *)0 == chip)
    {
        return 0;
    }
    idx = S_Idx(chip);
    if (idx >= TMC5160_CHIP_COUNT)
    {
        return 0;
    }
    raw = (int32_t)TMC5160_SpiRead(chip, REG_X_ENC);
    return raw - s_enc_offset[idx];
}

/**
 * @输入 chip: 芯片指针
 * @输出 int32_t: 编码器偏差绝对值 (X_ENC - XACTUAL)
 */
int32_t TMC5160_GetEncoderDeviation(TMC5160_CHIP_T *chip)
{
    int32_t enc_pos = TMC5160_GetEncoderPosition(chip);
    int32_t x_actual = TMC5160_GetPosition(chip);
    int32_t diff = enc_pos - x_actual;

    if (diff < 0)
    {
        diff = -diff;
    }
    return diff;
}

/**
 * @输入 chip: 芯片指针
 * @输出 无
 * @说明 在线清零位置计数器（XACTUAL/X_ENC 写 0，软件 offset 归零，
 *        清 ENC_STATUS 粘滞位）；调用方须保证已停稳且无回零在跑，
 *        否则减速段瞬态可能置 deviation_warn（终态帧如实上报）
 * 依据 TMC5160A_Datasheet_Rev1.14.ch06.p045.md: X_ENC 可写/ENC_STATUS 写1清
 */
void TMC5160_ZeroPosition(TMC5160_CHIP_T *chip)
{
    uint8_t idx;

    if ((void *)0 == chip)
    {
        return;
    }
    idx = S_Idx(chip);
    if (idx >= TMC5160_CHIP_COUNT)
    {
        return;
    }
    TMC5160_SpiWrite(chip, REG_XACTUAL, 0UL);
    TMC5160_SpiWrite(chip, REG_X_ENC, 0UL);
    s_enc_offset[idx] = 0;
    TMC5160_SpiWrite(chip, REG_ENC_STATUS, 0x03);
}

/**
 * @输入 chip: 芯片指针; timeout_ms: 超时(ms)
 * @输出 TMC5160_MOVE_RESULT_T
 * @说明 等待芯片完成定位，检查 RAMP_STAT.bit9(position_reached)
 * @注意 阻塞轮询：仅非任务上下文调用（如调度器启动前的单线程测试），
 *       禁在任务内用（5ms 粒度轮询会破坏任务节拍）
 */
TMC5160_MOVE_RESULT_T TMC5160_WaitPosition(TMC5160_CHIP_T *chip, uint32_t timeout_ms)
{
    uint32_t elapsed = 0;
    uint32_t ramp_stat;

    while (elapsed < timeout_ms)
    {
        ramp_stat = TMC5160_GetRampStat(chip);

        if (0xFFFFFFFF == ramp_stat || 0 == ramp_stat)
        {
            return TMC5160_MOVE_SPI_ERROR;
        }

        if (ramp_stat & (1UL << 9))
        {
            return TMC5160_MOVE_OK;
        }

        TMC5160_DelayMs(5);
        elapsed += 5;
    }

    return TMC5160_MOVE_TIMEOUT;
}

/* ==== 回零基础控制（StallGuard2 芯片级原语；状态机/时序在 app/homing） ==== */

/* SGT=0 起始值(ch13 §13.1, 阈值待真机交互调定, 待实测确认) / sfilt=0 非滤波
 * (ch13 §13.3) / SEMIN=0 CoolStep 关（仅用 StallGuard 堵转检测） */
#define TMC5160_HOME_COOLCONF      0x00000000UL
/* TCOOLTHRS=200：TSTEP=2^24/VMAX≈146 → 取 200 略高于
 * （ch13 §13.1 + ch05.p038 TCOOLTHRS≥TSTEP），SG 在≥约 1.46RPS 生效
 * 依据 .cl/memory/config.md home_tcoolthrs=200 */
#define TMC5160_HOME_TCOOLTHRS     200UL
/* AMAX=20000（与参数组 4 同量级）：a=20000×fCLK²/2^41≈2.05M µsteps/s²
 * 依据 .cl/memory/config.md home_amax=20000 */
#define TMC5160_HOME_AMAX          20000UL

/* 依据 TMC5160A_Datasheet_Rev1.14.ch06.p043.md:
 *   SW_MODE sg_stop=bit10（RAMP_EVENT_STOP_SG / SG_RESULT_MASK 已上移 tmc5160.h） */
#define TMC5160_SW_MODE_SG_STOP    (1UL << 10)

/**
 * @输入 chip: 芯片指针
 * @输出 无
 * @说明 配置 StallGuard2 回零检测：写 COOLCONF/TCOOLTHRS/AMAX→置 SW_MODE.sg_stop
 *        （纯写无现场保存；恢复由 TMC5160_HomeRestore 写常量标准值）
 * @注意 修改全局寄存器配置，配对 TMC5160_HomeRestore 恢复
 */
void TMC5160_HomeConfig(TMC5160_CHIP_T *chip)
{
    TMC5160_SpiWrite(chip, REG_COOLCONF, TMC5160_HOME_COOLCONF);
    TMC5160_SpiWrite(chip, REG_TCOOLTHRS, TMC5160_HOME_TCOOLTHRS);
    TMC5160_SpiWrite(chip, REG_AMAX, TMC5160_HOME_AMAX);
    TMC5160_SpiWrite(chip, REG_SW_MODE, TMC5160_SW_MODE_SG_STOP);
}

/**
 * @输入 chip: 芯片指针
 * @输出 无
 * @说明 恢复回零前标准值：SW_MODE=0（清 sg_stop）、TCOOLTHRS=0（关 SG 窗口）
 *        写常量而非存现场：复位残留在下次回零结束即被洗净（无读）
 */
void TMC5160_HomeRestore(TMC5160_CHIP_T *chip)
{
    TMC5160_SpiWrite(chip, REG_SW_MODE, 0UL);
    TMC5160_SpiWrite(chip, REG_TCOOLTHRS, 0UL);
}

/**
 * @输入 chip: 芯片指针
 * @输出 无
 * @说明 清 RAMP_STAT.event_stop_sg 锁存位（W1C 写，无读）；
 *        回零检测到即清、启动前也清，防锁存位跨次污染
 */
void TMC5160_HomeClearStall(TMC5160_CHIP_T *chip)
{
    TMC5160_SpiWrite(chip, REG_RAMP_STAT, TMC5160_RAMP_EVENT_STOP_SG);
}

/**
 * @输入 chip: 芯片指针
 * @输出 无
 * @说明 标定零点：XACTUAL 写 0（ch06.p040：homing 时允许改 XACTUAL）
 */
void TMC5160_HomeZero(TMC5160_CHIP_T *chip)
{
    TMC5160_SpiWrite(chip, REG_XACTUAL, 0UL);
}
