/**
 * @file YummyIMU_Protocol.cpp
 * @brief YummyIMU 兼容协议实现。
 * @details 默认输出 USE 姿态行；DEBUG 模式额外输出 SENSOR 行，便于上位机观察
 *          BMI088 原始加速度、校正角速度和 VQF 静止判定。INS、双 BMI270 等
 *          当前硬件不具备的功能明确返回 UNSUPPORTED。
 * @version 1.1
 * @date 2026-08-15 1.0 新增 YummyIMU 协议、BMI088 传感器遥测及静止判断。
 * @date 2026-08-15 1.1 改用定点格式化，并分离命令与遥测发送缓冲区。
 */

#include "YummyIMU_Protocol.h"
#include "bsp_usb.h"
#include "sys_debug.h"
#include <cmath>
#include <cstdlib>
#include <cstdio>
#include <cstring>

namespace {
uint32_t period_ms = 5U; // 默认 200 Hz，低于 YummyIMU USE 模式 500 Hz 上限
constexpr size_t kCommandBufferSize = 160U;
char command_buffer[kCommandBufferSize];
size_t command_length = 0U;
uint32_t elapsed_ms = 0U;
char command_tx_buffer[256];
char telemetry_tx_buffer[512];
enum class Mode : uint8_t { Use, Debug, Binary, Ins };
Mode mode = Mode::Use;

struct TextWriter {
  char *buffer;
  size_t capacity;
  size_t length = 0U;

  void AppendChar(char value) {
    if (length + 1U < capacity) buffer[length++] = value;
  }

  void AppendText(const char *text) {
    while (*text != '\0' && length + 1U < capacity) buffer[length++] = *text++;
  }

  void AppendUnsigned(uint64_t value) {
    char reversed[20];
    size_t count = 0U;
    do {
      reversed[count++] = static_cast<char>('0' + value % 10U);
      value /= 10U;
    } while (value != 0U && count < sizeof(reversed));
    while (count != 0U) AppendChar(reversed[--count]);
  }

  void AppendFloat(float value, uint8_t decimals) {
    static constexpr uint32_t powers_of_ten[] = {
      1U, 10U, 100U, 1000U, 10000U, 100000U, 1000000U};
    if (!std::isfinite(value) || decimals >= (sizeof(powers_of_ten) / sizeof(powers_of_ten[0]))) {
      AppendText("nan");
      return;
    }
    const uint32_t scale = powers_of_ten[decimals];
    const int64_t scaled = static_cast<int64_t>(
      std::lround(value * static_cast<float>(scale)));
    const bool negative = scaled < 0;
    const uint64_t magnitude = negative
      ? static_cast<uint64_t>(-scaled)
      : static_cast<uint64_t>(scaled);
    if (negative) AppendChar('-');
    AppendUnsigned(magnitude / scale);
    if (decimals == 0U) return;
    AppendChar('.');
    uint64_t divider = scale / 10U;
    const uint64_t fraction = magnitude % scale;
    for (uint8_t i = 0U; i < decimals; ++i) {
      AppendChar(static_cast<char>('0' + (fraction / divider) % 10U));
      divider /= 10U;
    }
  }

  void Send(void) {
    if (capacity == 0U) return;
    buffer[length < capacity ? length : capacity - 1U] = '\0';
    USB_Transmit_Data(reinterpret_cast<uint8_t *>(buffer),
                      static_cast<uint16_t>(length));
  }
};

void SendText(const char *text) {
  USB_Transmit_Data(reinterpret_cast<uint8_t *>(const_cast<char *>(text)),
                    static_cast<uint16_t>(std::strlen(text)));
}

void SendReply(const char *command) {
  const int n = std::snprintf(command_tx_buffer, sizeof(command_tx_buffer), "OK RECV:%s\n", command);
  if (n > 0) USB_Transmit_Data(reinterpret_cast<uint8_t *>(command_tx_buffer), static_cast<uint16_t>(n));
}

void ProcessCommand(char *command) {
  while (*command == ' ' || *command == '\t') ++command;
  const size_t len = std::strlen(command);
  while (len > 0U && (command[len - 1U] == ' ' || command[len - 1U] == '\t')) command[len - 1U] = '\0';
  if (std::strcmp(command, "LIST") == 0) {
    const int n = std::snprintf(command_tx_buffer, sizeof(command_tx_buffer), "FW:H7_BSP BMI088\nMODE:USE,DEBUG,BINARY\nIMU:BMI088\nRATE:%lu\n", static_cast<unsigned long>(1000U / period_ms));
    if (n > 0) USB_Transmit_Data(reinterpret_cast<uint8_t *>(command_tx_buffer), static_cast<uint16_t>(n));
    return;
  }
  if (std::strncmp(command, "MODE ", 5U) == 0) {
    const char *value = command + 5U;
    if (std::strcmp(value, "USE") == 0) mode = Mode::Use;
    else if (std::strcmp(value, "DEBUG") == 0) mode = Mode::Debug;
    else if (std::strcmp(value, "BINARY") == 0) mode = Mode::Binary;
    else if (std::strcmp(value, "INS") == 0) { SendText("ERROR:UNSUPPORTED INS\n"); return; }
    else { SendText("ERROR:BAD_MODE\n"); return; }
    SendReply(command); return;
  }
  if (std::strncmp(command, "RATE ", 5U) == 0) {
    const int rate = std::atoi(command + 5U);
    if (rate != 50 && rate != 100 && rate != 200) { SendText("ERROR:RATE 50|100|200\n"); return; }
    period_ms = 1000U / static_cast<uint32_t>(rate); SendReply(command); return;
  }
  if (std::strncmp(command, "STATUS", 6U) == 0) {
    const volatile Sys_Debug_IMU_Data_t &d = Debug_IMU_Data;
    TextWriter writer{command_tx_buffer, sizeof(command_tx_buffer)};
    writer.AppendText("STATUS:IMU=BMI088,REST=");
    writer.AppendUnsigned(d.VQF_Rest_Detected);
    writer.AppendText(",TEMP="); writer.AppendFloat(d.Temperature_C, 2U);
    writer.AppendText(",ACCEL_NORM="); writer.AppendFloat(d.Accel_Norm_m_s2, 3U);
    writer.AppendText(",GYRO_BIAS="); writer.AppendFloat(d.VQF_Gyro_Bias_Z_rad_s, 6U);
    writer.AppendText(",ACCEL_REJECT="); writer.AppendUnsigned(d.Accel_Update_Rejected_Counter);
    writer.AppendChar('\n'); writer.Send();
    return;
  }
  if (std::strncmp(command, "IMU ", 4U) == 0 || std::strncmp(command, "BAUD ", 5U) == 0 ||
      std::strncmp(command, "TEMP ", 5U) == 0 || std::strncmp(command, "CAL ", 4U) == 0 ||
      std::strncmp(command, "TDRIFT", 6U) == 0 || std::strncmp(command, "YAWCAL", 6U) == 0 ||
      std::strcmp(command, "INS START") == 0) {
    SendText("ERROR:UNSUPPORTED BMI088_SINGLE_IMU\n"); return;
  }
  SendText("ERROR:UNKNOWN_COMMAND\n");
}

void EmitTelemetry(void) {
  const volatile Sys_Debug_IMU_Data_t &d = Debug_IMU_Data;
  const float rad_to_deg = 57.295779513f;
  const float pitch = d.Euler_Pitch_rad * rad_to_deg;
  const float roll = d.Euler_Roll_rad * rad_to_deg;
  const float yaw = d.Euler_Yaw_rad * rad_to_deg;
  if (mode == Mode::Binary) {
    int16_t p = static_cast<int16_t>(std::lround(pitch * 100.0f));
    int16_t r = static_cast<int16_t>(std::lround(roll * 100.0f));
    int16_t y = static_cast<int16_t>(std::lround(yaw * 100.0f));
    uint8_t frame[8] = {0xA5, static_cast<uint8_t>(p), static_cast<uint8_t>(p >> 8), static_cast<uint8_t>(r), static_cast<uint8_t>(r >> 8), static_cast<uint8_t>(y), static_cast<uint8_t>(y >> 8), 0U};
    for (int i = 0; i < 7; ++i) frame[7] = static_cast<uint8_t>(frame[7] + frame[i]);
    USB_Transmit_Data(frame, sizeof(frame)); return;
  }
  if (mode == Mode::Debug) {
    TextWriter writer{telemetry_tx_buffer, sizeof(telemetry_tx_buffer)};
    const float angles[] = {pitch, roll, yaw, pitch, roll, yaw, pitch, roll, yaw};
    for (size_t i = 0U; i < (sizeof(angles) / sizeof(angles[0])); ++i) {
      if (i != 0U) writer.AppendChar(',');
      writer.AppendFloat(angles[i], 3U);
    }
    writer.AppendText("\nSENSOR,");
    writer.AppendFloat(d.Original_Accel_X_m_s2, 4U); writer.AppendChar(',');
    writer.AppendFloat(d.Original_Accel_Y_m_s2, 4U); writer.AppendChar(',');
    writer.AppendFloat(d.Original_Accel_Z_m_s2, 4U); writer.AppendChar(',');
    writer.AppendFloat(d.Corrected_Gyro_X_rad_s, 5U); writer.AppendChar(',');
    writer.AppendFloat(d.Corrected_Gyro_Y_rad_s, 5U); writer.AppendChar(',');
    writer.AppendFloat(d.Corrected_Gyro_Z_rad_s, 5U); writer.AppendChar(',');
    writer.AppendUnsigned(d.VQF_Rest_Detected); writer.AppendChar(',');
    writer.AppendFloat(d.VQF_Rest_Gyro_Deviation_Ratio, 4U); writer.AppendChar(',');
    writer.AppendFloat(d.VQF_Rest_Accel_Deviation_Ratio, 4U); writer.AppendChar(',');
    writer.AppendFloat(d.Temperature_C, 2U); writer.AppendChar('\n');
    writer.Send();
  } else {
    TextWriter writer{telemetry_tx_buffer, sizeof(telemetry_tx_buffer)};
    writer.AppendFloat(pitch, 3U); writer.AppendChar(',');
    writer.AppendFloat(roll, 3U); writer.AppendChar(',');
    writer.AppendFloat(yaw, 3U); writer.AppendChar('\n');
    writer.Send();
  }
}
} // namespace

extern "C" void YummyIMU_Protocol_Init(void) {
  command_length = 0U; elapsed_ms = 0U; period_ms = 5U; mode = Mode::Use;
  USB_Init(YummyIMU_Protocol_RxCallback);
}

extern "C" void YummyIMU_Protocol_RxCallback(uint8_t *buffer, uint16_t length) {
  for (uint16_t i = 0U; i < length; ++i) {
    const char c = static_cast<char>(buffer[i]);
    if (c == '\r' || c == '\n') {
      if (command_length != 0U) { command_buffer[command_length] = '\0'; ProcessCommand(command_buffer); command_length = 0U; }
    } else if (command_length + 1U < kCommandBufferSize) command_buffer[command_length++] = c;
    else command_length = 0U;
  }
}

extern "C" void YummyIMU_Protocol_Process_1ms(void) {
  if (++elapsed_ms >= period_ms) { elapsed_ms = 0U; EmitTelemetry(); }
}
