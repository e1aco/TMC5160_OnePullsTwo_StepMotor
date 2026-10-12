/*****************************************************************************
 * @文件: queue.h
 * @作者: cl
 * @日期: 2026-08-25
 * @版本: v1.0
 * @说明: 通用循环队列（纯数据结构，零硬件依赖）
 * @来源: 自 TMC5160_StepMotor(F407) 移植归一，行为等价
 ****************************************************************************/
#ifndef QUEUE_H
#define QUEUE_H

#include <stdint.h>

/* ==== 常量定义 ==== */
#define QUEUE_SIZE       48
#define QUEUE_ELEM_SIZE  8
#define QUEUE_OK         0
#define QUEUE_FULL       1
#define QUEUE_EMPTY      2
#define QUEUE_NULL_PTR   ((void *)0)

/* ==== 类型定义 ==== */
typedef struct {
    uint8_t  _queue[QUEUE_SIZE][QUEUE_ELEM_SIZE];
    uint8_t  head;
    uint8_t  tail;
    uint8_t  count;
} QUEUE_T;

/* ==== 全局实例 ==== */
extern QUEUE_T g_queue_st;

/* ==== 接口 ==== */
void    QUEUE_Init(QUEUE_T *me);
uint8_t QUEUE_Insert(QUEUE_T *me, uint8_t *value);
uint8_t QUEUE_Delete(QUEUE_T *me);
void   *QUEUE_First(QUEUE_T *me);
uint8_t QUEUE_IsFull(QUEUE_T *me);
uint8_t QUEUE_IsEmpty(QUEUE_T *me);
uint8_t QUEUE_GetCount(QUEUE_T *me);

#endif /* QUEUE_H */
