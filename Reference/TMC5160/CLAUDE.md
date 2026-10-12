# TMC5160 步进电机驱动项目

## 项目路径
`E:\Desktop\XM\XM_NBNZY\NBNZY-004-StepMotorDriver`

## 芯片资料位置
`NBNZY-004-08\TMC5160\` — 包含中英文数据手册、应用笔记、评估板资料

### 关键文档
| 文件 | 说明 |
|---|---|
| `TMC5160A_Datasheet_Rev1.14.pdf` | 英文数据手册(Rev1.14) |
| `TMC5160A_Datasheet_Rev1_13_CN.pdf` | 中文数据手册(Rev1.13) |
| `AN005a-IC_Package_PCB_Footprint_Guidelines_eTQFP48.pdf` | eTQFP48封装指南 |
| `AN028-Extending_the_positioning_range_of_TMC5XXX.pdf` | TMC5XXX定位范围扩展 |

## 驱动代码位置
`NBNZY-004-01\StepMotorDriver_Cmake\ELA_LIB\tmc5160\`

| 文件 | 说明 |
|---|---|
| `tmc5160.h` | 寄存器地址定义+结构体+函数原型 |
| `tmc5160.c` | SPI读写+模式配置实现 |
| `tmc5160_example.c` | 使用示例(SPI模式逐步学习: 初始化→斩波→定位→速度→堵转回零) |

## TMC5160 核心特性
- 电源: 8~60V DC
- 电流: 最高20A(需外部MOSFET)
- 封装: TQFP48(7x7mm) / QFN8x8
- 接口: SPI + 单线UART + STEP/DIR
- 微步: 最高256微步
- 核心技术: StealthChop2(静音), SpreadCycle(高动态), StallGuard2(堵转检测), CoolStep(节能75%), DcStep(负载自适应)

## 三种工作模式
| 模式 | SD_MODE | SPI_MODE | 说明 |
|---|---|---|---|
| 1 | 0 | 1 | 位置式步进(内部斜坡发生器) |
| 2 | 1 | 1 | STEP/DIR + SPI控制 |
| 3 | 1 | 0 | 简单STEP/DIR(独立运行) |

## 寄存器分组速查

### 通用配置(0x00-0x0C)
- `GCONF 0x00` — 全局配置(校准/PWM模式/方向/DIAG)
- `GSTAT 0x01` — 状态(复位/驱动错误/欠压)
- `SLAVECONF 0x03` — UART地址
- `X_COMPARE 0x05` — 位置比较(闭环用)
- `SHORT_CONF 0x09` — 短路保护
- `DRV_CONF 0x0A` — BBM/过温/驱动电流
- `GLOBAL_SCALER 0x0B` — 全局电流缩放

### 速度控制(0x10-0x15)
- `IHOLD_IRUN 0x10` — 保持/运行电流
- `TPWMTHRS 0x13` — StealthChop上限阈值
- `TCOOLTHRS 0x14` — StallGuard使能阈值
- `THIGH 0x15` — 高速切换阈值

### 运动控制(0x20-0x2D)
- `RAMPMODE 0x20` — 0定位/1正转/2反转/3保持
- `XACTUAL 0x21` — 实际位置(有符号32位)
- `VMAX 0x27` — 最大速度
- `AMAX 0x26` — 最大加速度
- `DMAX 0x28` — 最大减速度
- `XTARGET 0x2D` — 目标位置(写入触发运动)
- 注意: 地址0x29保留,跳过

### 斜坡/开关(0x33-0x36) — 回零和闭环关键
- `VDCMIN 0x33` — DcStep使能速度
- `SW_MODE 0x34` — 限位开关+失速事件配置
- `RAMP_STAT 0x35` — 到位标志/开关事件/失速
- `XLATCH 0x36` — 位置锁存

### 编码器接口(0x38-0x3D) — 位置闭环核心
- `ENCMODE 0x38` — 编码器模式(N触发/极性/清零)
- `X_ENC 0x39` — 编码器实际位置(有符号32位)
- `ENC_CONST 0x3A` — 累积常数(匹配分辨率)
- `ENC_DEVIATION 0x3D` — 偏差报警阈值

### 驱动配置(0x6C-0x73)
- `CHOPCONF 0x6C` — 斩波配置(微步/blank/chm/TOFF)
- `COOLCONF 0x6D` — CoolStep/StallGuard2配置
- `DRVSTATUS 0x6F` — StallGuard值+错误标志(只读)
- `PWMCONF 0x70` — PWM配置

## SPI通信协议(数据手册4.1.1节)
- 40位数据帧(5字节)
- 写: `[1|addr[6:0]] [data[31:24]] [data[23:16]] [data[15:8]] [data[7:0]]`
  - Byte0 = `0x80 | reg_addr`
- 读: `[0|addr[6:0]] [0x00] [0x00] [0x00] [0x00]` (需连续两次通信)
  - Byte0 = `reg_addr & 0x7F`
  - 第一次: 发送读请求,收到的是上一次通信的返回值(丢弃)
  - 第二次: 发送读请求,收到的是第一次请求的寄存器值

## SPI控制模式学习路径(SD=0, SPI=1)

此模式下芯片内部斜坡发生器自动处理加减速,只需通过SPI写目标位置即可.

### 学习步骤(对应example.c)
| 步骤 | 功能 | 关键寄存器 |
|---|---|---|
| step1 | 结构体初始化+引脚模式+电机电流 | IHOLD_IRUN |
| step2 | 斩波器配置(微步/SpreadCycle/迟滞) | CHOPCONF |
| step3 | 定位运动(设置速度曲线→写XTARGET) | RAMPMODE, VSTART~VMAX, A1~DMAX, XTARGET |
| step4 | 轮询RAMP_STAT等待到位 | RAMP_STAT(position_reached bit9) |
| step5 | 速度模式(连续正转/反转) | RAMPMODE=1/2, VMAX为目标速度 |
| step6 | 读取错误状态 | GSTAT, DRV_STATUS |
| step7 | StallGuard2堵转回零 | TCOOLTHRS, DRV_STATUS, COOLCONF |

### 速度曲线参数
```
VSTART →(A1)→ V1 →(AMAX)→ VMAX
              ←(D1)←    ←(DMAX)←
```
- VSTART: 启动速度, VSTOP: 停止速度(建议VSTOP≤VSTART)
- V1=0: 跳过A1/D1段,只用AMAX/DMAX
- 定位模式: VMAX是上限,目标由XTARGET决定
- 速度模式: VMAX就是目标速度

### CHOPCONF关键位域(0x6C)
| 位域 | 示例值 | 含义 |
|---|---|---|
| [27:24] MRES | 0000 | 256微步(一转=200×256=51200微步) |
| [16:15] TBL | 01 | Blank时间24clk≈2μs |
| [14] CHM | 0 | SpreadCycle模式 |
| [7:4] HSTRT | 0101(5) | 斩波迟滞开始 |
| [2:0] HEND | 101(3) | 斩波迟滞结束 |
| [3] | 1 | 数据手册要求固定为1 |

### StallGuard2堵转回零

**原理:** SG值(0~1023)反映负载,空载高(800+),堵转低(0~300),设中间阈值判定.

**前置条件(!易踩坑):**
1. TCOOLTHRS必须设,回零VMAX必须>TCOOLTHRS
2. CHOPCONF必须用SpreadCycle(CHM=0)
3. 机构必须有机械硬限位

**回零流程:**
```
清除错误 → 设TCOOLTHRS → 反转找限位 → 循环读SG值
→ SG<阈值→停止→设XACTUAL=0 → 退回脱离限位 → 最终零位
```

**回零参数(需实测调整):**
- HOMING_VELOCITY: 20000(回零速度,单位μsteps/s)
- HOMING_THRESHOLD: 500(SG低于此值判定堵转)
- HOMING_BACKOFF: 2000(退出限位的距离)

**DRV_STATUS(0x6F)读SG值:** `(drv >> 16) & 0x3FF`

## 当前状态
- 寄存器地址定义.h文件已补充完整(13→44个寄存器)
- SPI读写函数已修正(去除>>2移位+R/W位修正)
- 使用示例.c已重写为SPI模式逐步学习(7步从初始化到堵转回零)
- 项目分支: V1
- 下一步计划: 编码器配置+位置环闭环控制

## 编码器闭环要点(后续用)
- ENC_CONST = (电机每转微步数 ÷ 编码器每转脉冲数) × 65536
- 通过ENC_DEVIATION设置位置偏差报警
- 主循环中读X_ENC做反馈,修正XTARGET实现闭环
