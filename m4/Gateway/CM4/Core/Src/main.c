/* USER CODE BEGIN Header */
/**
 ******************************************************************************
 * @file           : main.c
 * @brief          : Main program body
 ******************************************************************************
 * @attention
 *
 * <h2><center>&copy; Copyright (c) 2022 STMicroelectronics.
 * All rights reserved.</center></h2>
 *
 * This software component is licensed by ST under BSD 3-Clause license,
 * the "License"; You may not use this file except in compliance with the
 * License. You may obtain a copy of the License at:
 *                        opensource.org/licenses/BSD-3-Clause
 *
 ******************************************************************************
 */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "ipcc.h"
#include "openamp.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
// 必须匹配 Linux A7 侧 rpmsg_tty 驱动，匹配后会生成 /dev/ttyRPMSG0
#define RPMSG_SERVICE_NAME "rpmsg-tty-channel"
#define SENSOR_MEMBER_ID "1"
#define SENSOR_TX_BUFFER_SIZE 192U
#define LED_TOGGLE_INTERVAL_MS 500U
// 当前为演示占位值，后续由 LoRa UART 接收到的外部终端数据替换
#define PLACEHOLDER_HEART_RATE_BPM 72U
#define PLACEHOLDER_TEMPERATURE_TENTHS_C 365
#define PLACEHOLDER_LATITUDE_1E4 399042L
#define PLACEHOLDER_LONGITUDE_1E4 1164074L

__IO FlagStatus rx_status = RESET;
uint8_t received_rpmsg[128];

static int rx_callback(struct rpmsg_endpoint *rp_chnl, void *data, size_t len, uint32_t src, void *priv);
static void ProcessSensorTask(struct rpmsg_endpoint *endpoint, uint32_t *last_poll_tick);
static void ProcessLedTask(uint32_t *last_led_tick);
static void SendSensorData(struct rpmsg_endpoint *endpoint, uint32_t tick_ms);

// 采集参数（可被A核指令动态调整）
volatile uint32_t sensor_poll_interval_ms = 1000; // 默认1秒采集一次
volatile uint8_t sensor_enable = 1;               // 采集使能标志
uint32_t sensor_tx_sequence = 0;

// M4 发给 A7 的业务字段缓存；小数统一用整数缩放，避免依赖浮点 printf
// temperature_tenths_c 表示 0.1°C，latitude_1e4/longitude_1e4 表示 1e-4 度
// 后续 LoRa 解析结果应写入这个结构体，再由 SendSensorData() 打包成 JSON
typedef struct
{
  uint16_t heart_rate_bpm;
  int16_t temperature_tenths_c;
  int32_t latitude_1e4;
  int32_t longitude_1e4;
} SensorData_t;
SensorData_t sensor_data;

void Read_LoRa_SensorData(SensorData_t *data)
{
  // TODO: 在这里接入 UART LoRa 接收解析逻辑
  data->heart_rate_bpm = PLACEHOLDER_HEART_RATE_BPM;
  data->temperature_tenths_c = PLACEHOLDER_TEMPERATURE_TENTHS_C;
  data->latitude_1e4 = PLACEHOLDER_LATITUDE_1E4;
  data->longitude_1e4 = PLACEHOLDER_LONGITUDE_1E4;
}
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
#ifdef __GNUC__
#define PUTCHAR_PROTOTYPE int __io_putchar(int ch)
#else
#define PUTCHAR_PROTOTYPE int fputc(int ch, FILE *f)
#endif
PUTCHAR_PROTOTYPE
{
  while ((USART3->ISR & 0X40) == 0)
    ;
  USART3->TDR = (uint8_t)ch;
  return ch;
}
/* USER CODE END 0 */

/**
 * @brief  The application entry point.
 * @retval int
 */
int main(void)
{
  /* USER CODE BEGIN 1 */
  struct rpmsg_endpoint resmgr_ept;
  uint32_t last_poll_tick = 0;
  uint32_t last_led_tick = 0;
  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  if (IS_ENGINEERING_BOOT_MODE())
  {
    /* Configure the system clock */
    SystemClock_Config();
  }

  /* IPCC initialisation */
  MX_IPCC_Init();
  /* OpenAmp initialisation ---------------------------------*/
  MX_OPENAMP_Init(RPMSG_REMOTE, NULL);

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART3_UART_Init();
  /* USER CODE BEGIN 2 */
  // 发布 rpmsg-tty-channel，A7 Linux 绑定 rpmsg_tty 后通过 /dev/ttyRPMSG0 通信
  OPENAMP_create_endpoint(&resmgr_ept, RPMSG_SERVICE_NAME, RPMSG_ADDR_ANY,
                          rx_callback, NULL);
  last_poll_tick = HAL_GetTick();
  last_led_tick = last_poll_tick;
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    OPENAMP_check_for_message();
    ProcessSensorTask(&resmgr_ept, &last_poll_tick);
    ProcessLedTask(&last_led_tick);
  }
  /* USER CODE END 3 */
}

/**
 * @brief System Clock Configuration
 * @retval None
 */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Initializes the RCC Oscillators according to the specified parameters
   * in the RCC_OscInitTypeDef structure.
   */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI | RCC_OSCILLATORTYPE_LSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = 16;
  RCC_OscInitStruct.HSIDivValue = RCC_HSI_DIV1;
  RCC_OscInitStruct.LSIState = RCC_LSI_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  RCC_OscInitStruct.PLL2.PLLState = RCC_PLL_NONE;
  RCC_OscInitStruct.PLL3.PLLState = RCC_PLL_NONE;
  RCC_OscInitStruct.PLL4.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }
  /** RCC Clock Config
   */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_ACLK | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2 | RCC_CLOCKTYPE_PCLK3 | RCC_CLOCKTYPE_PCLK4 | RCC_CLOCKTYPE_PCLK5;
  RCC_ClkInitStruct.AXISSInit.AXI_Clock = RCC_AXISSOURCE_HSI;
  RCC_ClkInitStruct.AXISSInit.AXI_Div = RCC_AXI_DIV1;
  RCC_ClkInitStruct.MCUInit.MCU_Clock = RCC_MCUSSOURCE_HSI;
  RCC_ClkInitStruct.MCUInit.MCU_Div = RCC_MCU_DIV1;
  RCC_ClkInitStruct.APB4_Div = RCC_APB4_DIV1;
  RCC_ClkInitStruct.APB5_Div = RCC_APB5_DIV1;
  RCC_ClkInitStruct.APB1_Div = RCC_APB1_DIV1;
  RCC_ClkInitStruct.APB2_Div = RCC_APB2_DIV1;
  RCC_ClkInitStruct.APB3_Div = RCC_APB3_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */
static void ProcessSensorTask(struct rpmsg_endpoint *endpoint, uint32_t *last_poll_tick)
{
  uint32_t now = HAL_GetTick();

  if (!sensor_enable || (now - *last_poll_tick < sensor_poll_interval_ms))
  {
    return;
  }

  *last_poll_tick = now;
  Read_LoRa_SensorData(&sensor_data);
  SendSensorData(endpoint, now); // tick_ms 用于 A7 判断采集时刻和缓存积压
}

static void ProcessLedTask(uint32_t *last_led_tick)
{
  uint32_t now = HAL_GetTick();

  if (now - *last_led_tick < LED_TOGGLE_INTERVAL_MS)
  {
    return;
  }

  *last_led_tick = now;
  HAL_GPIO_TogglePin(GPIOF, GPIO_PIN_3);
}

static void SendSensorData(struct rpmsg_endpoint *endpoint, uint32_t tick_ms)
{
  char tx_buffer[SENSOR_TX_BUFFER_SIZE];
  uint32_t seq = ++sensor_tx_sequence;
  int32_t temp_abs = sensor_data.temperature_tenths_c < 0 ? -sensor_data.temperature_tenths_c : sensor_data.temperature_tenths_c;
  int32_t lat_abs = sensor_data.latitude_1e4 < 0 ? -sensor_data.latitude_1e4 : sensor_data.latitude_1e4;
  int32_t lon_abs = sensor_data.longitude_1e4 < 0 ? -sensor_data.longitude_1e4 : sensor_data.longitude_1e4;
  // 每条 RPMsg 为一行 JSON，A7 侧按 JSON Lines 解析并写入 data.json/RingBuffer
  int tx_len = snprintf(tx_buffer, sizeof(tx_buffer),
                        "{\"seq\":%lu,\"tick_ms\":%lu,\"member_id\":\"%s\",\"heart_rate\":%u,\"temperature\":%s%ld.%01ld,\"latitude\":%s%ld.%04ld,\"longitude\":%s%ld.%04ld}\n",
                        (unsigned long)seq,
                        (unsigned long)tick_ms,
                        SENSOR_MEMBER_ID,
                        (unsigned int)sensor_data.heart_rate_bpm,
                        sensor_data.temperature_tenths_c < 0 ? "-" : "",
                        (long)(temp_abs / 10),
                        (long)(temp_abs % 10),
                        sensor_data.latitude_1e4 < 0 ? "-" : "",
                        (long)(lat_abs / 10000),
                        (long)(lat_abs % 10000),
                        sensor_data.longitude_1e4 < 0 ? "-" : "",
                        (long)(lon_abs / 10000),
                        (long)(lon_abs % 10000));

  if (tx_len <= 0)
  {
    return;
  }

  if ((size_t)tx_len >= sizeof(tx_buffer))
  {
    tx_len = sizeof(tx_buffer) - 1;
  }

  printf("%s", tx_buffer);

  if (is_rpmsg_ept_ready(endpoint))
  {
    OPENAMP_send(endpoint, tx_buffer, tx_len);
  }
}

static int rx_callback(struct rpmsg_endpoint *rp_chnl, void *data, size_t len, uint32_t src, void *priv)
{
  size_t copy_len = len;

  (void)rp_chnl;
  (void)src;
  (void)priv;

  if (data == NULL)
  {
    return -1;
  }

  if (copy_len >= sizeof(received_rpmsg))
  {
    copy_len = sizeof(received_rpmsg) - 1;
  }

  memcpy(received_rpmsg, data, copy_len);
  received_rpmsg[copy_len] = '\0';
  printf("received_rpmsg=%s\r\n", received_rpmsg);

  // A7 可通过 /dev/ttyRPMSG0 下发采集开关和周期调整命令
  if (strncmp((char *)received_rpmsg, "EN=0", 4) == 0)
  {
    sensor_enable = 0;
    printf("采集已关闭\r\n");
  }
  else if (strncmp((char *)received_rpmsg, "EN=1", 4) == 0)
  {
    sensor_enable = 1;
    printf("采集已开启\r\n");
  }
  else if (strncmp((char *)received_rpmsg, "PERIOD=", 7) == 0)
  {
    uint32_t val = (uint32_t)atoi((char *)received_rpmsg + 7);
    if (val > 0 && val < 60000)
    {
      sensor_poll_interval_ms = val;
      printf("采样周期已设为%lu ms\r\n", sensor_poll_interval_ms);
    }
  }

  rx_status = SET;
  return 0;
}
/* USER CODE END 4 */

/**
 * @brief  This function is executed in case of error occurrence.
 * @retval None
 */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */

  /* USER CODE END Error_Handler_Debug */
}

#ifdef USE_FULL_ASSERT
/**
 * @brief  Reports the name of the source file and the source line number
 *         where the assert_param error has occurred.
 * @param  file: pointer to the source file name
 * @param  line: assert_param error line source number
 * @retval None
 */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     tex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */

/************************ (C) COPYRIGHT STMicroelectronics *****END OF FILE****/
