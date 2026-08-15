/**
 * @file YummyIMU_Protocol.h
 * @brief YummyIMU 串口/USB CDC 兼容协议，提供姿态、加速度、角速度和静止状态。
 * @date 2026-08-15
 */

#ifndef YUMMYIMU_PROTOCOL_H
#define YUMMYIMU_PROTOCOL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void YummyIMU_Protocol_Init(void);
void YummyIMU_Protocol_Process_1ms(void);
void YummyIMU_Protocol_RxCallback(uint8_t *buffer, uint16_t length);

#ifdef __cplusplus
}
#endif

#endif
