# 项目状态 (State) — AI 维护，状态单源
> **单源声明**：任务队列 / 未决风险 / 时序测量表在此；`.cl/require.md` 只放**稳定需求**（目标/硬件池/引脚/调试回传）。
> **变化率=高频**：`/cl gon` 只读本文件 + `.cl/memory/`（不整读 require）。
> **更新时机**：`/cl run|code` 收尾改任务；`/cl tim` 回填时序；`/cl end` 归档（任务滚动 / 剥已解决风险入 memory 定论 / 清产物）。
> **reflash 待办已结（2026-09-27 结构对齐）**：`.embeddedskills/config.json` 已探测写入——gcc project+preset=Debug（构建 0E/0W/FLASH 61092B 验证）、openocd=stlink/stm32h7x/swd、jlink=STM32H750VB、serial=115200（探针未接/JLink.exe 缺失，运行期首调再补）；旧 `.cl/vendor/config.json` 线索与 cmake_build/hex 手写路径作废（产物单源 = `state.json` `last_build.artifacts`）。

## 未决风险 (Open Risks)
> 每个 /cl run|code 收尾标 [x]/[c]/[!] 前 AI 自查 9 类高风险区并登记（规则/词表唯一权威 = details/risk.md）。无风险的任务条目末尾标 `风险:无`。

| 日期 | 任务 | 类别 | 位置 | 假设/风险点 | 可追溯到? | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 2026-09-17 | 全工程 | 安全/看门狗 | 全工程 | 无 IWDG；主循环/外设挂死或堵转时电机持续通电（已确认工程无 IWDG/WWDG） | 缺口 | [x] 已解决（2026-09-20 IWDG 落地；2026-09-22 RTOS 移植引入 wdgTask 最低优先级喂狗，待烧录确认） |
| 2026-09-18 | 组5超高速 | 硬件时序/电气 | module/drv/tmc5160.c:94 | 组5 VMAX=2863311(50rps)在24V+传送带下必失速丢步（实测边界1200<stall≤1500rpm，U2先丢），丢步后开环位置丢失、无限位则无法0x09恢复，只能断电重建零点 | 实测 | [x] 已解决（组5已降600rpm生产档并验证PASS） |
| 2026-09-21 | 组6极限运行 | 失败路径/恢复 | module/drv/tmc5160.c:95 | 组6=1200rpm（原1442降速，距U2失速边界(1471,1481]约22%裕量），IRUN=27(4A)发热或下移边界：失速→位置丢失→断电重建；仅有人值守测试，生产帧禁用param=6，每次用后查失步位（2026-09-28 IRUN已改为29，见"IRUN29超额定"行）；**2026-09-29 实锤：重物压带+传送带下G6=1200rpm双机0.8s内失步（空载22%裕量被重载吃光），失步后位置基准已不可信，须断电/回零重建再谈生产使用**；2026-09-29 二分定案：重载双机最高不失步 **610rpm（60s认证）**，1200rpm 否决；G6 重载投产须 ≤610rpm 且投产前断电/回零重建校准 | 实测 | [✓] 人工确认（2026-09-29 用户接受 G6=610rpm 重载档） |
| 2026-09-20 | 看门狗 | 时序参数/经验值 | module/hal/wdg.c:14 | IWDG超时标称约5s（CubeMX /128+1249）；HAL先启动序列下同步正常，卡死约5s后复位如期发生；LSI绝对频率精度未量 | 实测 | [x] 已解决（功能验证通过，绝对精度待定） |
| 2026-09-20 | 看门狗 | 寄存器/手册细节 | module/hal/wdg.c | LSI/IWDG域不同步：IWDG1_SR恒0x03(PVU/RVU)，IWDG未启动，老化降级无狗运行；r2内存快照cycle 1→8推进正常 | 实测 | [✓] 人工确认（用户2026-09-22：历史0x03根因=运行过程中重新烧录所致，非IWDG未启动；RTOS宏版上电同步正常无WARN） |
| 2026-09-22 | RTOS 移植 | 实时性/竞态 | module/app/sched.c | 闭环读 1ms 快照，滞后 1 周期；G6 20rev/s 下 1ms≈1024µsteps>容差512，可能致补步震荡（T1） | 推演链 | [x] 已解决（2026-09-22 T1 实测 PASS：AGING G6 1200rpm 7轮 reach=1 dev=U1:12/U2:0 全≤512 无故障位 comm=0） |
| 2026-09-22 | RTOS 移植 | 实时性/竞态 | module/app/tasks.c | tmrTask(16) 的 RTT 1KB printf 若 >1ms，阻塞期间 closedLoopTask(24) 停摆（T3） | 实测 | [x] 已解决（tag6=266~282us<1ms，且16<24不抢占闭环，只记tmr自身抖动，2026-09-22） |
| 2026-09-22 | RTOS 移植 | 实时性/竞态 | module/drv/tmc5160.c:337,373 | S_SpiWrite/SpiRead 原 static 收发缓冲多任务共享必撕裂 → 已改局部变量；SPI 访问收敛到 ctrlTask（方案 c） | 实测(编译) | [x] 已解决 |
| 2026-09-22 | RTOS 移植 | 内存/栈 | module/app/tasks.c | 任务栈静态估算 ctrl2048/cl1024/tmr1024/wdg512 B（heap 15360B，用量≈5KB）；高水位未实测 | 实测 | [x] 已解决（[STK]余量ctrl1464/cl512/tmr472/wdg384B，最紧cl=50%/tmr=46%，均>30%，2026-09-22） |
| 2026-09-22 | RTOS 移植 | 安全/看门狗 | module/hal/wdg.c:84 | 降级路径 s_dog_on=0 时不喂狗，但 MX_IWDG1_Init 已启动 IWDG → 降级即 5s 复位环 | 推演链 | [x] 已解决（改恒喂，WDG_IsOn 降为诊断标志，2026-09-22） |

| 2026-09-22 | CAN老化 | 硬件时序/电气 | 板端FDCAN2(PB12/13)+PCAN-USB | 板端FDCAN2接收零触发：PCAN发5帧板端0回帧；根因=RTOS移植CubeMX重生成清零MessageRAM（ExtFilters/RxFifo0/TxFifo3→0，git 2f39e07实锤），fdcan.c已恢复；PCAN-50帧状态干净证发送侧正常 | 实测 | [x] 已解决（恢复后[CAN RX OK]正常，老化10轮PASS，2026-09-22） |
| 2026-09-23 | 摆法2老化 | 寄存器/手册 | module/drv/tmc5160.c:507 | TMC5160_Init 只清 GSTAT 不清 ENC_STATUS；J-Link 刷机/MCU 复位不复位 TMC5160，残留 deviation_warn 粘滞位污染下次 P0/终态判据（P0 st=03 实锤，窗内 peak=487<512 未超限但 encS=2） | 实测 | [x] 已解决（Init 补写 REG_ENC_STATUS=0x03；P0 stop 冒烟 st=01 双绿，2026-09-23） |
| 2026-09-23 | 摆法2老化 | 硬件时序/电气 | TMC5160 编码器偏差预算 | 静止 hw lag 累积至 -321~-487（占 512 预算 95%），G5 运动瞬态峰值 513/694 真超限致终态 st=03；与摆法2无关（HEAD ac133c9 同 TMC 状态下 bisect 同复现；TMC ramp 自主执行）。多次刷机无断电则 XACTUAL/X_ENC/静滞持续累积 | 实测 | [x] 已解决（24V 断电重建基线后 G5 10轮/20legs 全 PASS dev≤25；验收跑前须断电重建基线，2026-09-23） |
| 2026-09-23 | G6起停soak | 硬件时序/电气 | TMC5160 编码器偏差预算 | maxdev 单调爬升 13→207（+0.12µsteps/腿，1586腿/6502s），非失步（RTT es/gs/ds 全程0）系静滞累积；速率外推 3h≈344、G5 1h≈112，均 <tol 1024。但多次跑不重建基线会叠加 | 实测 | [ ] 待人工确认（实测数据：RESULT PASS legs=1586 retries=0 maxdev=207/1024，RTT 7028条遥测零故障，2026-09-23） |
| 2026-09-24 | 回零重构 | 失败路径/恢复 | module/app/homing.c | 校准门控：上电默认未校准（须先回零）；无挡板台架无法完成回零→回零失败/被任何命令打断即置未校准并锁死运动（靠 main.c HOME_ZeroAtPowerUp 调试旁路或重新上电恢复）；生产须保留挡板 | 实测 | [!] 待实测（挡板回零+双电机并发） |
| 2026-09-24 | 回零重构 | 寄存器/手册 | module/drv/tmc5160.c:864 | HomeRestore 写常量 SW_MODE=0/TCOOLTHRS=0（非存现场）；将来若启用限位开关写 SW_MODE，恢复值须同步改，否则回零后误关 | 推演 | [ ] 待人工确认 |
| 2026-09-24 | 回零重构 | 失败路径/恢复 | module/app/can_cmd.c | ESTOP 若带非法掩码(如0x06)走 INVALID_MASK 拒收，不断功率级（与当前门控规则一致）；如需 ESTOP 无条件断功率须特判 | 推演 | [ ] 待人工确认 |
| 2026-09-24 | 协议v2 | 数值溢出/定点 | tools/aging_can_10rounds.py | v2 到位反馈无位置值 → 上位机无法做位置闭环验证；aging/soak 的位置偏差判据失效，需重设计（改判到位标志或另行回传位置） | 推演 | [!] 待实测（aging/soak v2 移植） |
| 2026-09-24 | 协议v2 | 寄存器/手册 | module/drv/tmc5160.c | 新增 TMC5160_GetProfileVmax：0x04 速度取参数组 VMAX（非法组钳 1，同 ApplyProfile）；方向 Can[6]=0x00正/0xFF反 | 实测(编译) | [x] 已解决（协议全量 19/19 PASS） |
| 2026-09-24 | 协议v2 | 失败路径/恢复 | module/app/can_cmd.c | 0x05 回零方向 Can[6]=0x00负/0xFF正（用户确认稿补项）；0x06 即时完成逐电机各一帧；0x07 急停反馈一律 0xE1；未知命令/非法掩码回 0xE4/0xE3 | 实测 | [x] 已解决（协议全量 19/19 PASS） |
| 2026-09-24 | PID整定 | 实时性/动态 | module/app/closed_loop.c | PID 已接入但默认关闭（CL_OFF）；开环稳态偏差恒 13（容差 512 下 PID 永不触发）；若要 PID 生效须收紧容差或确认大偏差工况；PID 无抗饱和（仅输出钳位±200），死区内不推进以抑饱和 | 实测 | [ ] 待人工确认（整定判据） |
| 2026-09-24 | PID整定 | 实时性/动态 | module/app/closed_loop.c | 分组开环偏差(param1~5)：稳态全 0，瞬态线性无超调；PID 死区512永不触发 | 实测 | [x] 用户2026-09-24决定暂时不调（开环已够；PID 保持已接入但默认关闭，作安全网） |
| 2026-09-28 | IRUN29超额定 | 硬件时序/电气 | module/drv/tmc5160.c:435 | IRUN=29→4.31A RMS超电机额定4A约8%：堵转/连续大负载温升超标→退磁/OT关断；OT预警纳入判读，久转摸壳温 | 实测(配置) | [✓] 人工确认（用户2026-09-29：长时间重载测试后摸壳温正常） |
| 2026-09-30 | 协议v2.1烧录 | 失败路径/恢复 | embeddedskills jlink_exec.py | jlink flash 用模板 `connect`（无 device/无 SetResetType）在 DAP init 失败时仍报 verified=true（假烧录）；`verified` 不可单独作三真判据 | 实测 | [x] 已解决（根因：J-Link 需先 `exec SetResetType = 0` 才能 attach STM32H750VB，否则 `Failed to initialize DAP`；绕过=直连 JLink.exe `loadbin+verifybin`；skill 模板待上游补 device+SetResetType） |
> 类别词表(9类): 硬件时序/电气 | 实时性/竞态 | 数值溢出/定点 | 寄存器/手册 | 失败路径/恢复 | 内存/栈 | 安全/掉电 | 时序参数/经验值 | UB/可移植
> 可追溯到?: 手册 / 实测 / 推演链 / 缺口
> 状态: [ ] 待人工确认 / [✓] 人工确认 / [!] 待实测 / [x] 已解决（可归档删除）

## 时序测量表 (Timing Budget)

### 前置配置区
> 默认指标（预算比例/总利用率上限/绝对裕量/测试周期/词表）为 skill 级默认，唯一权威见 `details/tim.md`「默认指标」；项目覆盖在此改。
- 时序测试点 IO: PD14 (TMC_CLK, TIM4_CH3) — 可翻转供 Saleae/示波器捕获
- 探针默认位置: 函数级（ISR 入口/出口），语句级由条目位置列自定义覆盖

### 条目表
| 操作 | 位置/探针位置 | 理论值(依据) | 预算/判据 | 测试 | 实测值(日期) | 利用率 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| SPI3 TMC5160 读写周期 | S_SpiWrite/SpiRead 的 SelectChip→DeselectChip 全事务 | 写单事务 6.2us(40bit@6.45M)+轮询/片选开销；读双报+5us 帧间隔（SPI3=PLL3Q 103.2M÷16=6.45M, fSCK≤fCLK/2=7.5M） | fSCK 6.45≤7.5MHz 规格内；写/读事务达标 | 软件(DWT, 480MHz 基准)·每次变更都测 | 写 max 8us / 读 max 22us/10917cyc（含片选）(2026-09-22 P1后重测；收发路径未动，与09-20一致）；**09-27 分层重构后复测(J-Link RTT 宏版捕获)**：写 max 10us/5136cyc(n=11,离散±0.04%)、读 max 33us/16164cyc·avg 26.01us(n=326918)；**09-28 句柄优化复测(同法)**：写 max 8us/4234~4236cyc(n=17,±2cyc)、读 min10844·p50 10938·avg 10966·max11644(n=391839)≈基线 | — | [x] 09-28 句柄优化后复测**写8us/4236cyc·读p50 10938 ≈ 09-22 基线持平**（写 us=8∈[3840,4320)、读 +21~49cyc=+0.2~0.45%;读 max11644 为 SysTick ISR 尾部~1%样本,与代码无关);⚠09-27 首轮 Δ 归因定案=spi_port 按 chip_id 每原语查表×3(写)/×7(读)×Debug -O0 ≈ +0.55~0.9us/次(写5136/读avg12737),5b86aeb 未动 SPI 已排除、fSCK 6.45MHz 两期未变(.ioc/spi.c 核实);09-28 修复=事务级句柄 SPI_PortOf 置窗口外解析一次+窗口内句柄直通,收回 写-901cyc/读-1771cyc,生产版61264B(-112B);fSCK 6.45≤7.5MHz 规格内 Δ 不违判据;原 [x] 09-22 基线(写8/读22us·10917cyc)与 09-17(写4437/读11311)一致,时钟基准有效;HAL 旧值(/240 虚高2×)=写20/读54→实~10/~27；**2026-09-30 复核=本任务(6314dc8)未动 S_SpiWrite/S_SpiRead/SelectChip 事务体（git diff 零命中），测量窗口不变 → 无关，沿用 [x]** |
| TIM4 PWM 输出 (TMC_CLK) | PD14 示波器 | 15MHz（TIM4CLK=240MHz, PSC=0/ARR=15） | 15MHz ±1% | 硬件(Saleae)·仅首次测 | 软件比值 fCLK≈15.00MHz±0.01%（SOAK r3，非仪器）(2026-09-10) | — | [!] 示波器终验欠 |
| USART1 串口回传 | PB14 TX | USART1CLK=64MHz, 115200 8N1 | — | 软件(DWT)·每次变更都测 | (待测) | — | [ ] |
| 主循环单圈 | main while(1) tag2全事务/tag5心跳块 | 空载循环体约1us；1Hz心跳块含12×SPI读约625us | —(无周期条目，不设预算) | 软件(DWT 480MHz+RTT Ch0)·抽稀500:1 | 典型1us(p50)/max2us(1105cyc,2928样)；心跳圈625us/300367cyc(1Hz,3样)(2026-09-18) | — | [expired] 裸机tag2/tag5探针已不存在（RTOS后main为空循环）；覆盖关系转ctrlTask条目 |
| SpreadCycle 斩波频率 | Rsense 两端（示波器） | ~20-25kHz（TOFF=5, TBL=3, fCLK=15MHz; f≈1/(4×tOFF), tOFF=(24+32×TOFF)/fCLK） | 16-30kHz | 硬件(Saleae/示波器)·仅首次测 | | — | [!] |
| 任务A 传感/分发圈 | S_SenseTask 全圈 (tag2, 摆法2) | CAN分发+快采(3读×2)+闭环Tick+HOME+终态补读发帧+心跳+1Hz诊断块(tag5) | 周期 2ms（相对 osDelay）；Σ≤70% → 预算 1400us | 软件(DWT 480MHz)·每次结构变更都测 | 基线(单轮) 常规圈 317us max+tag5=358→最坏675=33.8%；**×3压测**(宏版快采×3=18读) 常规 861us+tag5=361→最坏1222=61.1%(2026-09-23) | 33.8%基线 / 61.1%×3 | [x] 基线Σ≈35%、×3压测Σ≈62% 均≤70% 通过；实测含[TM] printf=上界 |
| 闭环 Tick 段 | CLOSEDLOOP_Tick×2 段 (tag7, 任务A内) | 同拍算写，摆法2 并入任务A 2ms 节拍（原 closedLoopTask 已删） | Tick段<100us | 软件(DWT)·每次结构变更都测 | 1~2us max / 944~978cyc, n=39670(2026-09-23 摆法2后宏版重测) | ~0.1% | [x] 宏版；原 closedLoopTask 的 osDelayUntil 条目随任务下线，节拍覆盖转任务A tag2；G6 T1 已验 dev=12/0≤512（2026-09-22） |
| tmrTask 回传阻塞 | S_TmrTask RTT printf | RTT Ch0 1KB 缓冲，1Hz 双通道（UART 关/RTT 开）；摆法2 后遥测增 ds/gs/es 三字段 | <1ms期望（tmr低优不饿死感知，超限只记tmr抖动） | 软件(DWT)·RTOS 移植后 | 332~340us max(n=80)；改前 267~282us(2026-09-23 摆法2后宏版重测) | ~0.03% | [x] 宏版；T3关闭（340us<1ms+优先级16<任务A40不抢占） |
| 快照一致性 | SCHED_CollectFast XACTUAL→X_ENC | 快采 3 读×2电机（×3压测宏版=18读/圈）；**XACTUAL→X_ENC 真实间隔=紧邻 1 次 SPI 读=22us(tag1)**，vactual 读排在 x_enc 之后不进 skew 口径 | skew<容差512（期望<100µsteps） | 软件+实测·RTOS 移植后 | 基线全窗 269us；×3压测全窗 813us max(n=12610,≈269×3线性)；真实间隔仍 22us→G6下≈23µsteps+lag263=286<512(2026-09-23) | — | [x] 基线+×3压测均过；真实 skew 不随轮数变（轮内 x_act→x_enc 仍紧邻） |
| **Σ** | 全部周期任务 CPU 占用合计 | 任务A(基线33.8% / ×3压测61.1%) + 任务B≈1% + tmr 0.03% + wdg 忽略 | Σ≤70% | 软件(DWT)·每次结构变更都测 | 基线 Σ≈35%；×3压测 Σ≈62%（861+361最坏+B+tmr）(2026-09-23) | 35% / 62% | [x] 基线与×3压测均≤70% 通过 |

## 任务队列 (Task Queue)

### 2026-09-30
- [✓] CAN协议v2.1：ESTOP 全网关断（无视掩码）+ 0x09 读位置单读（每电机1次SPI）：代码/require附录A/上位机脚本(can_protocol_full、aging_can_10rounds)已改，编译 0E/0W FLASH 61544B，格式门禁零新增 **验收:人工**（P0-7 运动/功率强制）（起 2026-09-30 | 止 2026-09-30 | 依据:can_protocol_full PASS=22 FAIL=0（readpos_ALL pos U1/U2=0；estop_invalid_mask_still_cuts→0xE1；readpos_exempt_after_estop pos=795117）；烧录=JLink 直烧 loadbin+verifybin O.K.；skill flash 假成功已绕过 | 验收 2026-09-30 用户：急停物理断电确认"自由"）
- [✓] 急停物理断功率验证：estop_power_test（VEL 转动→ESTOP mask=0x00→0xE1→再发 VEL 被拒）M 全 PASS；电机断电后自由（用户 2026-09-30 手感确认）（起 2026-09-30 | 止 2026-09-30 | 依据:estop_power_test RESULT(M) PASS + 用户"自由"）
- [✓] aging_can_10rounds v2.1 冒烟：收集层改全量 ingest（修原 ACK 窗丢弃错位到位帧的竞态）；RESULT PASS rounds=1 fails=0，P0 U1=-14509/U2=51200，leg1/2 dev=+0 st=01 pst=00（起 2026-09-30 | 止 2026-09-30 | 验收 2026-09-30 用户确认）
- [✓] 0x09 加 Can[2] 位置源(0=XACTUAL/1=X_ENC) + soak_3h 移植 v2.1：G5 360s 冒烟 RESULT PASS legs=236 retries=0 maxdev=42；标定 X_ENC 与 XACTUAL 同量纲(ΔENC/ΔACT=0.999, 非×12.8)（起 2026-09-30 | 止 2026-09-30 | 依据:probe_scale Δ比值 + soak RESULT PASS | 验收 2026-09-30 用户确认）
- [x] G6 soak v2.1 复核（部分，用户 842s 处停止）：574 legs / retries=0 零故障，maxdev 26(前500s平)→32/37/42/47/52→82（842s），与历史 13→207 单调爬升同现象；远<tol 1024 **验收:人工**（起 2026-09-30 | 止 2026-09-30(中断) | 依据:soak_g6_console.log beat 序列；未达 3h 终点）
- [x] 固件内老化/整体测试套件（test/aging_test.c + hal/nvm.c）：pre-RTOS 阻塞、15s 启动延时、G1~G5 多档（G6 极限档禁用）、selftest(时钟/SPI/运动/编码器)+急停+LED+OT累计；结果存 BKPSRAM 环形（tools/read_nvm.py 读，跨复位/掉电保持）；观测走 RAM 状态 g_ag_status + RTT **验收:人工**（起 2026-09-30 | 止 2026-09-30 | 依据:G1~G5×1min 跑通 phase=5、BKPSRAM count=2 解码正确；正式 G1~G5×60min(~5h) 已启动，跑完自动停 | 待用户验收）
- [x] 固件内老化 G1~G3 实测（正式跑，意外断电于 G4 中断）：G1=312腿/G2=1221/G3=2899，maxdev 恒 **39**(<tol512)，全程 retries=0 零失步/零故障位；温度(外部测温中) TMC≤80℃/MOS≤90℃/DCDC-12V≤85℃，OT/OTPW=0；报告已出 `桌面\测试计划表及总结报告_驱动板整体测试_20260930.xlsx` **验收:人工**（起 2026-09-30 | 止 2026-09-30(断电) | 依据:BKPSRAM+read_nvm.py；G4/G5 未完成）

### 2026-09-29
- [✓] 重载双电机最高不失步速度二分（用户指定：直接改 G6 VMAX 寻值，G5=600rpm 锚点、U=1200 已知失步，tol≤30rpm；探针 10s+熔断 dev>640 即停，0x08 在线重建基线免断电；末验 60s 达标后 G6=认证值固化）：**认证 610rpm（raw 581841），区间 (618.75, 637.5] rpm**；探针判据=速度模式编码器滞后 max|XACTUAL-X_ENC|（0x04 下 CLOSEDLOOP dev 恒=-enc 不可用，已修正）；轨迹 600P/618.75P/637.5S(4.8s)/675S/750S/900S；**⚠生产警告：多轮滑脱后绝对零点已失效，G6 新档投产前必须断电/回零重建+重新校准（0x08 重建的是相对基线不是零点）** **验收:人工**（P0-7 运动/功率强制）（起 2026-09-29 | 止 2026-09-29 | 依据:60s认证610rpm maxlag=216/512 es/gs=0 无OT BKP0R不变 0x04/0x07 ACK齐 + bisect_log.csv | 验收 2026-09-29 用户确认：610rpm 接受为重载档）

### 2026-09-28
- [✓] SPI端口层事务句柄优化+复测持平基线：spi_port新增不透明SPI_PortBus（SPI_PortOf窗口外解析+窗口内句柄直通），tmc5160 SpiWrite/SpiRead窗口零查表；复测写8us/4236cyc(n=17)读p50 10938(n=391839)≈09-22基线（起 2026-09-28 | 止 2026-09-28 | 验收 RTT宏版复测+生产版61264B烧回[TM]=0；构建0E/0W；format_gate FAIL163/WARN94零新增）风险:见时序表SPI行
- [✓] 运行电流IRUN 27→29(4.02→4.31A RMS)：超电机额定4A，用户选29档；tmc5160.c REG_IHOLD_IRUN+memory tmc5160_irun同步（起 2026-09-28 | 止 2026-09-28 | 验收 构建0E/0W+烧录verified+遥测正常；⚠运动中CS_ACTUAL=29未捕获待补测）风险:见风险日志"IRUN29超额定"
- [✓] 重物压带双电机速度探索(G5基点向上找最快不失步)：U1+U2并发0x04速度模式G6=1200rpm连续1min；判据编码器偏差≤512µsteps且es/gs/ds全0；G6首试卡死(阶段未区分，用户已断电重启；免回零，上电已校准直测) **验收:人工**（P0-7 运动/功率强制，禁自动[✓]；kit不适用=CMake路线走原子闭环）（起 2026-09-28 | 止 2026-09-29 | 依据:有效边界数据=重载G6秒级失步：0x04后~0.8s U1/U2 enc冻结(303897/269043±13)而act以1.02Mµsteps/s爬至61.5M，dev≫512；ds全程OT/OTPW=0且sg_result=0x1D高载签名、停机后bit31=1 stall；gs=00；es=02失步位持续；60.14s跑满+0x04/0x07 ACK均收到、0x07到位帧未到(失步伴生)；BKP0R 5A5A0003→5A5A0003零复位+RTT无BOOT横幅 | 验收 2026-09-29 用户确认）
### 2026-09-24
- [✓] 不同运动组开环偏差测试：param=1~5 各 REL 1圈(U1)，稳态偏差全 0，瞬态线性无超调（11.4s/2.85s/1.1s/0.55s/0.2s）；容差512下PID永不触发，整定判据待定（起 2026-09-24 | 止 2026-09-24 | 验收 RTT [CLD] 20Hz）风险:见风险日志"PID整定"
- [✓] PID接入闭环+开环偏差时序测试：closed_loop 按电机双PID实例、Tick 改 PID_Calculate（内部修正不复位）+ RTT 20Hz 偏差遥测；开环实测快档<0.5s/慢档11.4s到位，稳态偏差恒13（两次一致），无超调振荡，远小于容差512（起 2026-09-24 | 止 2026-09-24 | 验收 RTT [CLD] 20Hz；构建 0E/0W FLASH 61092B；format_gate 0FAIL）风险:见风险日志"PID整定"
- [✓] CAN协议v2重写+全命令验证：命令帧Can[0]=cmd/Can[1]=电机位掩码/Can[2]=参数组/Can[3..6]=值大端；命令码0x01~0x08（0x05回零/0x06闭环/0x07停止急停/0x08清零）；反馈ACK/到位(cmd/motor/param/到位/过温/驱动异常)/错误帧(Can[0..6]全=0xE1-E4)；校准门控（起 2026-09-24 | 止 2026-09-24 | 验收 can_protocol_full v2 PASS=19 FAIL=0；构建 0E/0W FLASH 60612B；format_gate 0FAIL）风险:见风险日志"协议v2"
### 2026-09-23
- [ ] 任务重组摆法2+CAN分发简化：任务A(收拆CAN+就地执行+快照ACK+闭环+回零+发终态)/任务B(仅快照到位判定)+迁app/can_cmd.c+删sense服务FIFO（起 2026-09-23 | 止 进行中；G5 aging/2轮G6/G6起停2h soak/Σ≤70%时序门禁轮 均已过（Σ=35%），待回零(挡板)/急停复测）
### 2026-09-22（早期条目已归档删节，长链见 git 历史，git 兜底）
