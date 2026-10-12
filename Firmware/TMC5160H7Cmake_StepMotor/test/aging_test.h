/*****************************************************************************
 * @文件: aging_test.h
 * @作者: cl
 * @日期: 2026-09-30
 * @版本: v1.0
 * @说明: 内建老化/整体自测接口（pre-RTOS，行为受 module/config.h CL_AGING_TEST
 *        与 CL_AG_TEST_MASK 门控；生产空宏零开销）
 * @依赖: config.h
 ****************************************************************************/
#ifndef AGING_TEST_H
#define AGING_TEST_H

#include "config.h"

/* pre-RTOS 阻塞式老化/整体自测；由 main 在 osKernelStart 前调用
 *（仅 #if CL_AGING_TEST 时 main 调用；未开启时本符号无定义，不参与链接） */
void AGING_SelfTest(void);

#endif /* AGING_TEST_H */
