/**
 * @file BMI088_Task.cpp
 * @brief BMI088 高优先级姿态解算任务。
 * @author zzm
 * @version 1.3
 * @date 2026-08-15 1.1 在任务上下文即时发起FIFO后续传输并逐帧解算
 * @date 2026-08-15 1.2 在任务与内核就绪后启动TIM8的500us服务
 * @date 2026-08-16 1.3 改用INT3数据就绪中断，移除TIM8采集启动
 *
 * @details
 * INT3数据就绪中断发起首笔SPI DMA；接收回调在需要续传或FIFO样本入队后置位线程
 * 标志。本任务被唤醒后先在任务上下文发起后续DMA，再逐帧调用Calculate()清空队列，
 * 避免约2kHz的陀螺仪数据积压；完成解算后更新调试遥测数据。
 */

/* Includes ------------------------------------------------------------------*/
#include "user_task.h"
#include "bsp_bmi088.h"
#include "sys_debug.h"

/* Private macros ------------------------------------------------------------*/

/* Private types -------------------------------------------------------------*/

/* Private variables ---------------------------------------------------------*/

/* Function prototypes -------------------------------------------------------*/
/**
 * @brief 执行 BMI088 FIFO 样本的高优先级姿态解算任务。
 * @param argument CMSIS-RTOS 任务入口参数，当前未使用。
 * @details
 * 任务阻塞等待SPI接收回调发出的续传或样本就绪标志。续传在底层SPI事务锁释放后由
 * 任务发起，避免DMA回调重入；随后持续解算，直到单生产者/单消费者队列为空。
 */
extern "C" void BMI088_Task(void *argument) {
  // 姿态解算依赖高频陀螺仪数据，提升任务优先级以降低 FIFO 排队延迟。
  osThreadSetPriority(osThreadGetId(), osPriorityHigh2);
  for (;;) {
    const uint32_t task_flags = osThreadFlagsWait(
        BMI088_TASK_FLAG_ALL, osFlagsWaitAny, osWaitForever);

    if ((task_flags & BMI088_TASK_FLAG_TRANSFER_SERVICE) != 0U) {
      // 底层SPI回调退出后事务锁已释放，可在任务上下文安全启动下一笔DMA。
      BSP_BMI088.Task_Service_Transfer();
    }

    bool sample_processed = false;
    while (BSP_BMI088.BMI088_Gyro.Get_Queue_Depth() != 0U) {
      // Calculate() 每次从陀螺仪队列取出一帧；循环排空可避免高频数据积压。
      BSP_BMI088.Calculate();
      sample_processed = true;
    }

    if (sample_processed) {
      // 发布最新姿态与诊断数据，供上位机调试和运行状态监控。
      Sys_Debug_IMU_Update();
    }
  }
}
