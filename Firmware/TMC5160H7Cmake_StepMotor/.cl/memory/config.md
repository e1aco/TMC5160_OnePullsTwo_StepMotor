# .cl/memory/ — 配置推导值（terse）

> 一行一条 key=value；≤200行·12KB；详见 details/init.md。
> 排查过程（s2gb/Kelvin）→ 原 `experience/log.md`（reflash 已删，git 历史可查）；全量快照见 git 8db5a7b。

## 定论（已否决 / 已解决）
spi_speed_downgrade = 否决: 降速论证伪  @SOAK r3 0翻转  #实测  2026-09-10
tmc_clk_12m_s2gb = 否决: 降 12MHz 可解 s2gb(enc=3072 更差)  @log 09-11  #实测  2026-09-11
u2_s2gb_sw = 否决: 软件假说穷尽(双模/DRVS/降速)  @log 09-10/11  #实测  2026-09-11
spi_spe_always_on = 否决: SPE 常开可省时(A/B tag0 -0.13us≈0)  @实测  #实测  2026-09-17
u2_s2gb = 已解决: B相100%丢步, RS-B开尔文跳线错位  @ManualRun  #你定案  2026-09-11
iwdg_cfg_order = 否决: 先配后启动致SR恒0x03, H7先0xCCCC启动  @r1-r3  #实测  2026-09-20
iwdg_mpu_patch = 否决: MPU重映射未改变SR卡死  @r3  #实测  2026-09-20
iwdg_hal_seq = 定案: HAL序列同步正常+卡死复位PASS  @RTT  #实测  2026-09-20
fdcan_msg_ram_zeroed = 已解决: 重生成清零致收发全死  @2f39e07  #实测  2026-09-22
can_init_rtt_print = 已解决: Init内RTT打印致CELL停,删即通  @对照  #实测  2026-09-22
p0_ack_motor_echo = v2:ACK回显cmd;终态按电机各发  @附录A  #实测  2026-09-24
aging_hist = 板端阻塞老化已删,走PCAN脚本  @log  #实测  2026-09-22
chip_table = s_chips单源; 掩码按位遍历  @tmc5160.h/c  #推导  2026-09-22
spi_delta_0927 = 定案: 5b86aeb未动SPI; Δ=端口层每原语查表, 句柄化收回写901/读1771cyc  @RTT  #实测  2026-09-28
dual_power_domain = 定案: MCU吃USB/JLink电, TMC5160吃24V; 复位须声明域, 24V断电不清MCU校准态  @G6重载测试踩坑  #实测  2026-09-28
g6_heavy_load_usable = 定案: 重载双机最高不失步610rpm(60s认证PASS), G6=1200rpm否决(0.8s失步); 重载生产用G6须≤610rpm  @bisect+60s  #实测  2026-09-29
irun29_thermal_ok = 已解决: 长时间重载测试后摸壳温正常, 温升风险关闭(IRUN=29=4.31A RMS超额定8%)  @重载二分全程  #你  2026-09-29
jlink_resettype_h7 = 定案: exec SetResetType=0才attach H750, 否则DAP fail; skill模板缺→假成功  @实测  2026-09-30
can_v2p1_estop = 定案: 0x07/0xFF无视掩码关全电机回0xE1; 0x09 Can[2]选源0=XACTUAL/1=X_ENC  @附录A  #实测  2026-09-30
x_enc_scale_1to1 = 实测: ΔENC/ΔACT=0.999, X_ENC已是µsteps当量(非×12.8), 可直接比  @R 0x39  #实测  2026-09-30
nvm_bkpsram = 定案: 测试结果存后备SRAM 0x38800000 环形(56B/条x78), 跨复位保持; 读 tools/read_nvm.py  @NVM2  #实测  2026-09-30
tmc_temp = 定案: TMC5160只有OT/OTPW过温标志位(>120/143C)无绝对温度; 绝对值需外接测温  @DRVSTATUS  #datasheet  2026-09-30
aging_pre_rtos = 定案: 固件内老化 pre-RTOS(config.h CL_AGING_TEST), 需15s启动延时+循环喂狗; G6极限档禁用  @test/aging_test.c  #实测  2026-09-30
thermal_meas = 实测(外部测温,老化中): TMC5160芯≤80C, MOS≤90C, DCDC-12V≤85C; TMC OT/OTPW标志0  @人工  #实测  2026-09-30
aging_g1g3_0930 = 实测: 固件内老化 G1=312腿/G2=1221/G3=2899, maxdev恒=39(<tol512), 全程retries=0无失步; G4中断/G5未跑  @BKPSRAM  #实测  2026-09-30
aging_g4_0930 = 实测: 固件内老化 G4 单档 60min legs=5264 maxdev=64(<tol512) 无失步/无故障位  @g_ag_status  #实测  2026-09-30
aging_g5_0930 = 实测: 固件内老化 G5(600rpm) 60min legs=10257 maxdev=90(<512) 无失步, NVM已存#0  @BKPSRAM  #实测  2026-09-30
aging_g6_0930 = 实测: G6(610rpm)长时老化~47min/8639腿失步 maxdev=29760(>tol512)失步位置位 → 上限=600rpm(G5)  @g_ag_status  #实测  2026-09-30
aging_g5_142min = 实测: G5(600rpm) 142min legs=24274 maxdev=64(<512) 零失步 零过温(ot=0); NVM#0  @g_ag_status  #实测  2026-10-08

## 电机参数 (57CME13)
motor_phases = 2  @电机规格书  #推导  2026-08-24
motor_hold_torque = 1.3 N·m  @电机规格书  #推导  2026-08-24
motor_step_angle = 1.8°  @电机规格书(200步/圈)  #推导  2026-08-24
motor_rated_current = 4 A  @电机规格书  #推导  2026-08-24
motor_phase_resistance = 0.42 Ω  @电机规格书  #推导  2026-08-24
motor_phase_inductance = 1.6 mH  @电机规格书  #推导  2026-08-24
motor_rotor_inertia = 3e-5 kg·m²  @0.3kg·cm²换算  #推导  2026-08-24
motor_weight = 0.8 kg  @电机规格书  #推导  2026-08-24
motor_fullsteps_per_rev = 200  @1.8°步距角  #规格书  2026-08-24
motor_microsteps = 256  @CHOPCONF.MRES  #推导  2026-08-24
motor_counts_per_rev = 51200  @200×256  #实测  2026-09-01

## MOSFET (AOD4126)
mosfet_vds = 100 V  @AOD4126  #推导  2026-08-24
mosfet_id_max = 43 A  @AOD4126 @VGS=10V  #推导  2026-08-24
mosfet_rds_on = 24 mΩ  @AOD4126 @VGS=10V typ  #推导  2026-08-24
mosfet_qg = 28 nC typ  @AOD4126  #推导  2026-08-24
mosfet_qgd = 10 nC typ  @AOD4126  #推导  2026-08-24
mosfet_vgs_th = 3.3 V typ  @AOD4126  #推导  2026-08-24

## 外围电容 (TMC5160A 应用)
board_bootstrap_cap = 470 nF ×4  @Qg>20nC→470nF, 每半桥一只  #推导  2026-09-10
board_12vout_cap = 10 µF  @≥10×自举电容→取上限  #推导  2026-09-10
board_pump_caps = CPI-CPO 22nF/100V, VCP-VS 100nF/16V  @ch02.p013  #datasheet  2026-09-10
board_vs_bulk_cap >= 100µF/A  @ch03.p015(4A→≥400µF)  #datasheet  2026-09-10

## TMC5160 驱动配置
tmc5160_rs = 0.05 Ω  @硬件资源池  #你  2026-08-24
tmc5160_vfs = 0.325 V  @ch28(VSRT)  #推导  2026-08-24
tmc5160_ifs = 6.5 A  @VFS/RS  #推导  2026-08-24
tmc5160_globalscaler = 256  @init 未写 GLOBALSCALER(复位默认)  #代码核对  2026-09-10
tmc5160_irun = 29  @IHOLD_IRUN; (29+1)/32×6.5/√2=4.31A RMS; 超电机额定4A,盯温升  #你  2026-09-28
tmc5160_ihold = 8  @IHOLD_IRUN; (8+1)/32×6.5/√2≈1.29A; 3 锁不住回调  #你  2026-09-20
tmc5160_ihold_delay = 8  @IHOLD_IRUN.IHOLDDELAY; 2^18×8/15MHz≈140ms  #代码核对  2026-09-10
tmc5160_tpowerdown = 40  @TPOWERDOWN; 2^18×40/15MHz≈699ms  #代码核对  2026-09-10

## TMC5160 时钟
tmc5160_clk_source = external  @PD14 TIM4_CH3 PWM  #你  2026-08-24
tmc5160_clk_freq = 15 MHz  @TIM4 PWM; XACTUAL 17881/s 匹配 15.00MHz±0.01%  #实测  2026-09-11
tmc5160_spi_max_sck = 7.5 MHz  @ch4 fSCK=fCLK/2  #推导  2026-09-11

## TMC5160 SPI 配置
tmc5160_spi_mode = 3  @ch4 (CPOL=HIGH,CPHA=2EDGE)  #推导  2026-08-24
tmc5160_spi_datasize = 8-bit  @40bit 数据报=8bit 地址+32bit 数据  #推导  2026-08-24
stm32_spi_prescaler = 16  @SPI3 kernel PLL3Q 103.2MHz ÷16=6.45MHz  #实测校准  2026-09-11
stm32_spi_baudrate = 6.45M  @103.2/16; 写9.2/读23.6us  #实测校准  2026-09-17
stm32_spi_kernel_clk = 103.2 MHz  @PLL3 HSE8/M5 ×N129 /Q2  #CubeMX  2026-09-09
tmc5160_tCSH_us = 5  @ch04 §4.3 (2×tCLK+10ns=320ns); 帧后加 5us  #datasheet  2026-09-17

## TMC5160 通讯事实
tmc5160_spi_status_byte = 0x38  @ch04 S4.1.2; 静止0x38  #datasheet+实测  2026-09-09
tmc5160_ihold_irun_readable = false  @ch05.p038 (只写, 回读恒 0)  #datasheet+实测  2026-09-09
tmc5160_enc_const_readable = false  @ENC_CONST标W; 保真用XTARGET  #datasheet+实测  2026-09-10
tmc5160_spi_fidelity_6p45M = verified  @SOAK r3 XTARGET 222170 帧 0 翻转  #实测  2026-09-10
tmc5160_spi_all_zero_reads = 无VM供电MISO恒0  @判活用非零签名  #实测  2026-09-10
u1_chip_populated = true  @U1 已焊 + SPI/CAN 测试通过  #实测  2026-09-16

## 无编码器回零 (StallGuard2)
home_velocity_rps = 2  @ch13 §13.4 (1~5RPS)  #推导  2026-09-12
home_vmax = 114532  @V=2×51200; 102400×2^24/15e6  #推导  2026-09-12
home_amax = 20000  @与参数组4同量级 → a≈2.05M µsteps/s²  #推导  2026-09-12
home_tcoolthrs = 200  @ch13 §13.1 (TSTEP≈146→取 200)  #推导  2026-09-12
home_sgt = 0  @ch13 §13.1 起始值; 阈值待真机调定  #待实测确认  2026-09-12
home_sg_confirm = 8  @连续零确认抗抖  #你  2026-09-12
home_backoff = 2048 microsteps  @防卡死  #你  2026-09-12
home_sg_poll_ms = 2  @fullstep 周期@2RPS=2.5ms  #推导  2026-09-17
home_run_timeout_ms = 30000  @机械行程未知 → 30s 兜底  #待实测确认  2026-09-17
home_bo_timeout_ms = 10000  @回退 2048≈64ms → 10s 兜底  #待实测确认  2026-09-17

## 编码器配置
encoder_ppr = 1000 PPR  @ENC_CONST=12.8 反推 200×256/12.8=4000cpr÷4  #实测  2026-09-01
encoder_cpr = 4000 counts/rev  @1000PPR ×4 正交  #推导  2026-09-01
encoder_resolution = 0.09°/count  @360°/4000  #推导  2026-09-01
encoder_enc_const = 12.8  @ENC_CONST=FSC×USC/cpr  #推导  2026-09-01
encoder_encmodes = 0x00  @源工程 ENCMODE 写入值  #源工程  2026-09-01
encoder_tol = 512 microsteps  @TMC5160_ENC_TOLERANCE  #实测  2026-09-18
encoder_lag_max = 263  @G6巡航滞后(回落13)  #实测  2026-09-18

## TMC5160 栅极驱动 / 斩波
tmc5160_drvstrength = 2  @AOD4126 Qgd=10nC(中等强度)  #推导  2026-08-24
tmc5160_bbmtime = 16  @死区 200ns typ 防直通  #推导  2026-08-24
tmc5160_chopconf = 0x000181C5  @TOFF5/HSTRT4/HEND3/TBL3/MRES0  #代码核对  2026-09-10
chopper_toff = 5  @ch01.p052 CHOPCONF[3:0]  #推导  2026-08-24
chopper_tbl = 3  @CHOPCONF[16:15]=54clk  #代码核对  2026-09-10
chopper_hstart = 4  @CHOPCONF[6:4]  #代码核对  2026-09-10
chopper_hend = 3  @CHOPCONF[10:7]  #代码核对  2026-09-10

## FDCAN2 配置
fdcan2_baud = 500 kbit/s  @NominalPrescaler=1/Seg1=13/Seg2=2, 16TQ@8MHz  #CubeMX  2026-09-16
fdcan2_autoretransmit = ENABLE  @CubeMX  #CubeMX  2026-09-16
fdcan2_msgram = EF=1/RxFifo0=8/TxFIFO=3  @CubeMX 重生成需保留  #源工程  2026-09-16

## STM32 时钟树
stm32_hse = 8 MHz  @.ioc  #推导  2026-08-24
stm32_sysclk = 480 MHz  @PLL1 8/1×120/2  #推导  2026-08-24
stm32_hclk = 240 MHz  @HPRE=DIV2  #推导  2026-08-24
stm32_apb1 = 120 MHz  @D2PPRE1=DIV2  #推导  2026-08-24
stm32_apb2 = 120 MHz  @D2PPRE2=DIV2  #推导  2026-08-24
stm32_tim4clk = 240 MHz  @APB1×2  #推导  2026-08-24
stm32_spi3clk = 103.2 MHz  @PLL3Q(2026-09-09 改)  #CubeMX  2026-09-17
stm32_dwt_cyccnt_clk = 480 MHz  @DWT=CPU=SYSCLK(D1CPRE=1); 实测 481MHz  #实测  2026-09-17
stm32_usart1clk = 64 MHz  @HSI  #推导  2026-08-24
stm32_fdcanclk = 8 MHz  @HSE  #推导  2026-08-24

## 调试通道
dbg_channel = RTT  @config.h RTT_DBG=1/UART_DBG=0  #用户  2026-09-18
rtt_buffer_up = 1024 B  @SEGGER_RTT_Conf.h BUFFER_SIZE_UP  #推导  2026-09-18

## 主循环时序
main_loop_typ_us = 1  @main while(1) tag2抽稀500:1  #实测  2026-09-18
main_loop_worst_us = 625  @1Hz心跳圈tag5(含12×SPI读)  #实测  2026-09-18

## 高速边界 (24V+传送带)
vmax_stall_U2_rpm = (1462,1481]  @vel二分+RTT enc冻结判  #实测  2026-09-18
vmax_stall_U1_rpm = (1500,1572]  @U1@1481零滑动, 1572丢  #实测  2026-09-18
vmax_stall_loaded_g6 = 重载(重物压带)双机G6=1200rpm 0.8s内失步:enc冻结act照常1.02M/s爬,dev≫512  @RTT窗口443228-514374  #实测  2026-09-29
vmax_stall_loaded_dual = (618.75, 637.5] rpm  @bisect_log 重载双机速度模式最高不失步区间; 637.5rpm 4.8s失步/675rpm<1s失步  #实测  2026-09-29
g6_loaded_stall_signature = ds=0x001D00xx(sg0x1D,OT/OTPW=0,停机后bit31=1) gs=00 es=02; 0x07到位帧不发(act-enc差61M PID不可达)  @RTT+CAN  #实测  2026-09-29
g6_loaded_60s_integrity = 60.14s跑满零复位 BKP0R 5A5A0003→5A5A0003 无BOOT  @jlink read-mem  #实测  2026-09-29
vel_cmd_unit = 寄存器原值  @SetVelocity直写VMAX, 物理=值/1.1178  #实测  2026-09-18
profile5_vmax = 572303  @10rps×51200×2^24/15e6, 600rpm生产档  #推导  2026-09-18
profile5_amax = 28000  @40000试探干净×0.7, 56rev/s²  #实测  2026-09-18
profile6_amax = 39200  @40000试探干净×0.98, 极限语义  #实测  2026-09-18
profile14_amax = 28000  @40000试探干净×0.7, G1~G4统一  #实测  2026-09-18
profile6_vmax = 581841  @重载认证610rpm(2026-09-29二分: L=618.75PASS→floor10档), raw=rpm×953.84; 原1144606(1200rpm)重载0.8s失步已否决  #实测  2026-09-29
profile6_scope = 极限运行专用  @生产禁用·值守·用后查失步  #你  2026-09-18

## 老化测试 (2026-09-20)
wdg_boot_counter = RTC BKP0R  @跨复位持久; POR首启=1,需RTC时钟+DBP  #推导  2026-09-20

## FreeRTOS (2026-09-22)
rtos_kernel = FreeRTOS V10.6.2 + CMSIS-RTOS2 封装  @Middlewares  #CubeMX  2026-09-22
rtos_tick = 1000 Hz  @configTICK_RATE_HZ; 1 tick=1ms  #CubeMX  2026-09-22
rtos_timebase = TIM1  @HAL tick 迁离 SysTick(.ioc NVIC.TimeBase)  #CubeMX  2026-09-22
rtos_heap = 15360 B  @configTOTAL_HEAP_SIZE(heap_4); 4 任务用量≈5KB  #推导  2026-09-22
rtos_task_ctrl = ctrlTask/prio28/1024B/1ms  @快照到位判定,不碰SPI  #推导  2026-09-23
rtos_task_sense = 任务A High(40)/2048B/2ms/SPI+CAN TX唯一  @tasks.c  #推导  2026-09-23
rtos_task_tmr = tmrTask/BelowNormal(16)/1024B/1s  @app/tasks.c  #推导  2026-09-22
rtos_task_wdg = wdgTask/Low(8)/512B/200ms  @恒喂; 低优饿死停喂  #推导  2026-09-22
rtos_spi_owner = senseTask 唯一SPI3 owner（无mutex, by design）  @app/sense.c  #你  2026-09-22
rtos_spi_param = S_SpiTransfer(SPI_TypeDef *)  @tmc5160.c; 双 SPI 双总线预留  #你  2026-09-22
rtos_ctrl_loop_us = 219  @tag2 max256样全同; sense 2ms降于旧255us  #实测  2026-09-22
rtos_taskA_loop_us = 317/675 基线, 861/1222 ×3压测  @tag2+tag5  #实测  2026-09-23
rtos_sigma_pct = 35% 基线 / 62% ×3压测  @任务A+B+tmr; ≤70%门禁  #实测  2026-09-23
rtos_snap_skew_us = 269/813 全窗, 真实X→ENC=22  @tag3+tag1  #实测  2026-09-23
rtos_spi_x3_stress = PASS  @快采×3宏版18读; tag2=861 tag3=813 Σ62%  #实测  2026-09-23
rtos_tmr_block_us = 340  @tag6 max n=80; 摆法2后遥测+ds/gs/es(改前282)  #实测  2026-09-23
rtos_cl_tick_us = 1~2  @tag7 max 944~978cyc n=39670; 摆法2并入任务A  #实测  2026-09-23
rtos_stk_free = sense1848/ctrl896/tmr552/wdg384B  @[STK]生产版复验  #实测  2026-09-23
mcu_reset_note = J-Link复位不清TMC寄存器  @act残留512000  #实测  2026-09-22
aging_can10 = PASS 10/10轮G5; dev≤26; leg1.2s  #实测  2026-09-22
fdcan_msg_ram = Ext1/RxFifo8/TxFifo3  @CubeMX保留; 2f39e07  #实测  2026-09-22
rtos_t1_g6_dev = PASS dev=U1:12/U2:0  @G6 7轮reach=1无故障cs_run=27; 闭L88  #实测  2026-09-22
rtt_logger_path = C:\Program Files\SEGGER\JLink_V932\JLinkRTTLogger.exe  #实测  2026-09-22
soak_g6_2h = PASS legs=1586 retries=0 maxdev=207 t=6502s  @soak_run.ps1  #实测  2026-09-23
soak_maxdev_drift = +0.12/腿静滞累积; 3h外推344<1024  @RTT  #实测  2026-09-23
rtt_drv_status_mask = 0x1E003000  @v2用OT26/25+GSTAT1  #代码核对  2026-09-23
soak_stale_frame_fix = drain+wait_ack+zeroed+rebaseline  @soak_3h.py  #实测  2026-09-23
