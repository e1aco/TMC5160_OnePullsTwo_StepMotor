/*****************************************************************************
 * @文件: nvm.h
 * @作者: cl
 * @日期: 2026-09-30
 * @版本: v1.0
 * @说明: 板载非易失留存（STM32H7 后备 SRAM BKPSRAM 0x38800000，4KB）
 *        环形记录最近 N 次测试结果，跨复位保持（VBAT 接则掉电亦保持）
 * @依赖: HAL(PWR/备份域)
 ****************************************************************************/
#ifndef NVM_H
#define NVM_H

#include <stdint.h>

#define NVM_PROFILE_COUNT  5U   /* 记录 G1~G5 五档 */

/* 单次测试记录（56B） */
typedef struct {
    uint32_t seq;                        /* 递增序号 */
    uint32_t boot;                       /* 本次 boot 计数 */
    uint32_t st_ok;                      /* selftest 1=PASS */
    uint32_t ot_seen;                    /* 运行中过温标志累计（bit2=ot/otpw，0=从未过热） */
    uint32_t legs[NVM_PROFILE_COUNT];    /* 各档 leg 数 */
    int32_t  maxdev[NVM_PROFILE_COUNT];  /* 各档 maxdev */
} nvm_result_t;

/* 使能备份域并校验魔数（无效则初始化头部） */
void NVM_Init(void);

/* 追加一条记录（环形覆盖最旧） */
void NVM_Append(const nvm_result_t *r);

/* 经 RTT 回放已存记录（默认最近 8 条） */
void NVM_Dump(void);

/* 已存记录总数 */
uint32_t NVM_Count(void);

#endif /* NVM_H */
