/*****************************************************************************
 * @文件: queue.c
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: 通用循环队列实现（纯数据结构，零硬件依赖）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一，行为等价
 * @注意: 非中断安全——生产者(CAN RX ISR)与消费者(主循环)共享时，
 *        count/head/tail 无临界区保护（与源工程行为等价，竞态风险已知）
 ****************************************************************************/
#include "algo/queue.h"

/* ==== 全局实例 ==== */
QUEUE_T g_queue_st;

/* ==== 接口实现 ==== */

/**
 * @输入 me: 队列指针
 * @输出 无
 * @说明 初始化队列（头尾指针清零，计数清零）
 */
void QUEUE_Init(QUEUE_T *me)
{
    me->head  = 0;
    me->tail  = 0;
    me->count = 0;
}

/**
 * @输入 me: 队列指针; value: 待入队数据指针(8字节)
 * @输出 QUEUE_OK(0) 成功 / QUEUE_FULL(1) 队满
 * @说明 拷贝数据到队尾，队满拒绝
 */
uint8_t QUEUE_Insert(QUEUE_T *me, uint8_t *value)
{
    uint8_t i;

    if (me->count >= QUEUE_SIZE)
    {
        return QUEUE_FULL;
    }

    for (i = 0; i < QUEUE_ELEM_SIZE; i++)
    {
        me->_queue[me->tail][i] = value[i];
    }

    me->tail = (me->tail + 1) % QUEUE_SIZE;
    me->count++;

    return QUEUE_OK;
}

/**
 * @输入 me: 队列指针
 * @输出 QUEUE_OK(0) 成功 / QUEUE_EMPTY(2) 队空
 * @说明 移动队头指针，删除一个元素
 */
uint8_t QUEUE_Delete(QUEUE_T *me)
{
    if (me->count == 0)
    {
        return QUEUE_EMPTY;
    }

    me->head = (me->head + 1) % QUEUE_SIZE;
    me->count--;

    return QUEUE_OK;
}

/**
 * @输入 me: 队列指针
 * @输出 队头元素地址（不移除），队空返回 NULL
 * @说明 获取队头数据指针，供上层只读访问
 */
void *QUEUE_First(QUEUE_T *me)
{
    if (me->count == 0)
    {
        return QUEUE_NULL_PTR;
    }

    return &me->_queue[me->head];
}

/**
 * @输入 me: 队列指针
 * @输出 1 满 / 0 未满
 * @说明 判断队列是否已满
 */
uint8_t QUEUE_IsFull(QUEUE_T *me)
{
    return me->count >= QUEUE_SIZE;
}

/**
 * @输入 me: 队列指针
 * @输出 1 空 / 0 非空
 * @说明 判断队列是否为空
 */
uint8_t QUEUE_IsEmpty(QUEUE_T *me)
{
    return me->count == 0;
}

/**
 * @输入 me: 队列指针
 * @输出 当前队列元素个数
 * @说明 读取队列计数
 */
uint8_t QUEUE_GetCount(QUEUE_T *me)
{
    return me->count;
}

