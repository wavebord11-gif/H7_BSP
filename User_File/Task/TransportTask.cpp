/**
 * @file    TransportTask.cpp
 * @brief   传输任务 —— USB CDC 初始化与遥测输出
 * @author  zzm
 * @version 1.3
 * @date    2026-07-11 1.2 移除未使用的 PID tuner
 * @date    2026-08-15 1.3 接入独立 YummyIMU USB CDC 协议模块。
 */

/* Includes ------------------------------------------------------------------*/

#include "user_task.h"
#include "usb_device.h"
#include "YummyIMU_Protocol.h"

/* Private macros ------------------------------------------------------------*/

/* Private types -------------------------------------------------------------*/

/* Private variables ---------------------------------------------------------*/

/* Private function declarations ---------------------------------------------*/

/* Function prototypes -------------------------------------------------------*/

extern "C" void Transport_Task(void *argument)
{
    MX_USB_DEVICE_Init();
    YummyIMU_Protocol_Init();
    for (;;)
    {
        YummyIMU_Protocol_Process_1ms();
        osDelay(1);
    }
}
