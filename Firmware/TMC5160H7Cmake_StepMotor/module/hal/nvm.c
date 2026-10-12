/*****************************************************************************
 * @文件: nvm.c
 * @作者: cl
 * @日期: 2026-09-30
 * @版本: v1.0
 * @说明: 后备 SRAM(BKPSRAM) 环形测试记录；跨复位保持（VBAT 接则掉电保持）
 *        布局: [0]=magic [1]=count，记录自字偏移 4 起，每条 13 字(52B)，容量 78 条
 * 依据 RM0433(STM32H7): 备份 SRAM 0x3880_0000(4KB)，访问须 PWR DBP + BKPRAM 时钟
 * @依赖: stm32h7xx_hal(PWR/备份域), hal/rtt_dbg
 ****************************************************************************/
#include "hal/nvm.h"
#include "stm32h7xx_hal.h"
#include "hal/rtt_dbg.h"

#define NVM_BASE_ADDR   0x38800000UL          /* 后备 SRAM 基址（RM0433） */
#define NVM              ((volatile uint32_t *)NVM_BASE_ADDR)
#define NVM_MAGIC       0x4E564D32UL          /* "NVM2"（加 ot_seen 字段） */
#define NVM_HDR_WORDS   4U                     /* magic,count,rsvd,rsvd */
#define NVM_REC_WORDS   14U                    /* sizeof(nvm_result_t)/4 = 56/4 */
#define NVM_TOTAL_WORDS 1024U                 /* 4KB/4 */
#define NVM_REC_MAX     ((NVM_TOTAL_WORDS - NVM_HDR_WORDS) / NVM_REC_WORDS)

/* ==== 接口实现 ==== */

void NVM_Init(void)
{
    HAL_PWR_EnableBkUpAccess();
    __HAL_RCC_BKPRAM_CLK_ENABLE();
    (void)HAL_PWREx_EnableBkUpReg();
    if (NVM_MAGIC != NVM[0])
    {
        NVM[0] = NVM_MAGIC;
        NVM[1] = 0U;
    }
}

uint32_t NVM_Count(void)
{
    return (NVM_MAGIC == NVM[0]) ? NVM[1] : 0U;
}

void NVM_Append(const nvm_result_t *r)
{
    volatile uint32_t *p;
    uint32_t cnt;
    uint32_t idx;
    uint8_t i;

    if ((void *)0 == r)
    {
        return;
    }
    NVM_Init();
    cnt = NVM[1];
    idx = cnt % NVM_REC_MAX;
    p = &NVM[NVM_HDR_WORDS + idx * NVM_REC_WORDS];
    p[0] = cnt;                /* seq = 序号 */
    p[1] = r->boot;
    p[2] = r->st_ok;
    p[3] = r->ot_seen;
    for (i = 0U; i < NVM_PROFILE_COUNT; i++)
    {
        p[4U + i] = r->legs[i];
        p[4U + NVM_PROFILE_COUNT + i] = (uint32_t)r->maxdev[i];
    }
    NVM[1] = cnt + 1U;         /* 提交 */
}

void NVM_Dump(void)
{
    uint32_t cnt;
    uint32_t start;
    uint32_t n;

    cnt = NVM_Count();
    RTT_DBG_Printf("[NVM] records=%lu magic=%s\r\n", (unsigned long)cnt,
                   (NVM_MAGIC == NVM[0]) ? "ok" : "bad");
    start = (cnt > 8U) ? (cnt - 8U) : 0U;
    for (n = start; n < cnt; n++)
    {
        volatile uint32_t *p = &NVM[NVM_HDR_WORDS + (n % NVM_REC_MAX) * NVM_REC_WORDS];

        RTT_DBG_Printf("[NVM] #%lu boot=%lu ok=%lu ot=%lu G1=%lu/%ld G2=%lu/%ld "
                       "G3=%lu/%ld G4=%lu/%ld G5=%lu/%ld\r\n",
                       (unsigned long)p[0], (unsigned long)p[1],
                       (unsigned long)p[2], (unsigned long)p[3],
                       (unsigned long)p[4], (long)p[9],
                       (unsigned long)p[5], (long)p[10],
                       (unsigned long)p[6], (long)p[11],
                       (unsigned long)p[7], (long)p[12],
                       (unsigned long)p[8], (long)p[13]);
    }
}
