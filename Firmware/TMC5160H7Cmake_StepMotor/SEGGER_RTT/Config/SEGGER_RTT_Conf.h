/*********************************************************************
*                   (c) SEGGER Microcontroller GmbH                  *
*                        The Embedded Experts                        *
*                           www.segger.com                           *
**********************************************************************

---------------------------END-OF-HEADER------------------------------
Purpose : User configuration file for RTT.
          For available configuration,
          refer to SEGGER_RTT_ConfDefaults.h.

----------------------------------------------------------------------
*/

#ifndef SEGGER_RTT_CONF_H
#define SEGGER_RTT_CONF_H

/*********************************************************************
*
*       Defines, configurable
*
**********************************************************************
*/

// 最大上行通道数（默认 3，我们用 1 个就够）
#define SEGGER_RTT_MAX_NUM_UP_BUFFERS       (3)
#define SEGGER_RTT_MAX_NUM_DOWN_BUFFERS     (3)

// 上行缓冲区大小（MCU→PC，字节）
#define SEGGER_RTT_BUFFER_SIZE_UP           (1024)

// 下行缓冲区大小（PC→MCU，字节）
#define SEGGER_RTT_BUFFER_SIZE_DOWN         (16)

// RTT 控制块对齐（Keil ARMCC 不支持 __attribute__((aligned))，用默认即可）
// #define SEGGER_RTT_ALIGNMENT               4

#endif
/*************************** End of file ****************************/
