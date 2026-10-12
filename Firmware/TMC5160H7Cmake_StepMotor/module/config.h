/*****************************************************************************
 * @文件: config.h
 * @作者: cl
 * @日期: 2026-09-09
 * @版本: v1.0
 * @说明: 工程编译期特性开关（调试通道唯一门控源）
 *        通道类(DBG_OPEN/UART_DBG/RTT_DBG)：控制调试输出通断；
 *        关闭时接口层用空宏剥除调用（编译期零开销，实参不求值）
 *        上电测试钩子已删除（2026-09-22 清场：SPI 系列宏与 AGING_TEST 归档删，
 *        老化走 PCAN 脚本 tools/aging_can_10rounds.py）
 * @消费: 通道类经 hal/uart_dbg.h、hal/rtt_dbg.h include 生效
 * @现状: UART 关（2026-09-18 切 RTT）；RTT 开（J-Link 回传主通道）
 ****************************************************************************/
#ifndef CONFIG_H
#define CONFIG_H

/* 总闸(DBG_OPEN)：1=按通道开关输出调试信息；
 * 0=无视通道配置，全部调试输出关闭（下方 #if 强制两通道为 0）
 * 消费: hal/uart_dbg.h、hal/rtt_dbg.h（编译期空宏剥除） */
#define DBG_OPEN    1

/* 上电测试钩子已删除（2026-09-22 清场）：SPI_SOAK/SPI_QUIET/SPI_MANUAL/
 * SPI_PRODRUN/SPI_PRODRUN_U1/AGING_TEST 归档删，老化走 PCAN 脚本 */

/* 看门狗总闸 WDG_ENABLE：独立看门狗 IWDG（CubeMX /128+1249，超时标称约 5s）
 * 取值: 1=WDG_Init 确认启动 + wdgTask 周期喂狗（恒喂，WDG_IsOn 仅诊断）；
 *       0=不确认不喂狗
 * 消费: Core/Src/main.c(#if WDG_ENABLE→WDG_Init()) + app/tasks.c wdgTask
 * 实现: module/hal/wdg.c（HAL 封装，启动由 Core/Src/iwdg.c 完成）
 * 现状=1：看门狗护航中 */
#define WDG_ENABLE  1

#if DBG_OPEN
/* 通道开关 UART_DBG：1=USART1(PB14/PB15, 115200)调试输出开（回传主通道）；
 * 0=该通道关闭。仅 DBG_OPEN=1 时本值有效 */
#define UART_DBG    0   /* USART1 调试输出 —— 已关闭（2026-09-18 切 RTT） */
/* 通道开关 RTT_DBG：1=SEGGER RTT 输出开；0=关。
 * 2026-09-18: J-Link 就位，开启 RTT 替代 USART1 调试回传。
 * 仅 DBG_OPEN=1 时本值有效 */
#define RTT_DBG     1   /* SEGGER RTT 输出 (J-Link RTT Viewer) */
#else
/* 总闸关闭时两通道强制为 0（与上方取值无关） */
#define UART_DBG    0
#define RTT_DBG     0
#endif

/* ===== 内建老化/整体自测（pre-RTOS；2026-09-30） =====
 * 总闸 CL_AGING_TEST：1=main 在 osKernelStart 之前调用 AGING_SelfTest()；
 *   0=生产空宏零开销（默认）。
 * 测试项 CL_AG_TEST_MASK 位选（同一宏决定测什么）：
 *   bit0 SELFTEST 基础通路(时钟/SPI通信/双机小位移/编码器一致性)
 *   bit1 AGING    多档老化 G1~G6（每档 CL_AG_SEG_MIN 分钟）
 *   bit2 THERMAL  温升（beat 内附 OT/OTPW；芯片/电机温度人工点温）
 *   bit3 ESTOP    急停（空转→MOTOR_EStop；人工确认自由滑停）
 *   bit4 LED      指示（LED_MCU 闪示）
 * 消费: Core/Src/main.c(#if CL_AGING_TEST) + test/aging_test.c
 * 注意: pre-RTOS 阻塞运行期间无 CAN（仅 RTT）；IWDG 已由 MX_IWDG1_Init 启动
 *       (~5s)，老化循环内须周期 WDG_Feed 喂狗 */
#define CL_AGING_TEST        1

/* 测试项位掩码（决定测什么；按位 or） */
#define CL_AG_TEST_SELFTEST  0x01U
#define CL_AG_TEST_AGING     0x02U
#define CL_AG_TEST_THERMAL   0x04U
#define CL_AG_TEST_ESTOP     0x08U
#define CL_AG_TEST_LED       0x10U
#define CL_AG_TEST_MASK      0x1FU

/* 老化参数 */
#define CL_AG_PROFILES       0x10U    /* G5 单档（600rpm）；跑到 17:40 自动停 */
#define CL_AG_SEG_MIN        142U     /* 142min（15:18→约17:40） */
/* 依据 .cl/memory/config.md motor_counts_per_rev=51200 → 每腿 1 圈 */
#define CL_AG_TRAVEL         51200L
/* 依据 drv/tmc5160.h TMC5160_ENC_TOLERANCE=512（失步判定 |XACTUAL-X_ENC| 上限） */
#define CL_AG_TOL            512U
/* 启动延时（秒）：上电/烧录后先不动电机，留稳定窗口（期间喂狗） */
#define CL_AG_START_DELAY_S  15U

#endif /* CONFIG_H */
