# STM32MP157 极地终端网关项目说明

## 1. 项目简介

本项目基于 STM32MP157 双核异构平台，实现面向极地科考场景的终端数据采集、缓存与展示链路。系统由 Cortex-M4 侧负责接收外部终端传感器数据并打包，通过 RPMsg/OpenAMP 发送给 Cortex-A7 Linux 侧；A7 侧负责数据缓存、Web 后端、前端展示以及后续网络恢复后的数据上传。

当前项目已经完成 M4 与 A7 的 RPMsg 通信链路、A7 端数据桥接服务、持久化环形缓存、Flask API 和 React 前端展示闭环。后续主要工作是将 M4 侧当前占位数据替换为 UART LoRa 模块接收到的真实外部终端传感器数据。

## 2. 系统架构

```text
外部传感终端
    ↓ LoRa
UART LoRa 模块
    ↓ UART
STM32MP157 M4
    ↓ RPMsg/OpenAMP JSON Lines
/dev/ttyRPMSG0
    ↓
STM32MP157 A7 rpmsg_bridge.service
    ├── /home/root/PSRT_app/backend/data.json
    ├── /home/root/PSRT_app/backend/rpmsg_cache.jsonl
    ↓
Flask 后端 API
    ↓
React 前端页面
```

### M4 侧职责

- 接收 LoRa UART 模块输入的数据。
- 维护采样开关和采样周期。
- 为每条数据添加 `seq` 和 `tick_ms`。
- 将业务字段打包成 JSON Lines。
- 通过 RPMsg endpoint `rpmsg-tty-channel` 发送到 A7。

### A7 侧职责

- 通过 Linux `rpmsg_tty` 驱动访问 `/dev/ttyRPMSG0`。
- 运行 `rpmsg_bridge.py` 常驻服务。
- 解析 M4 发送的 JSON 数据。
- 写入 Flask 后端使用的 `data.json`。
- 写入持久化环形缓存 `rpmsg_cache.jsonl`。
- 后续在配置远端上传地址后，实现网络恢复优先补传缓存数据。

## 3. 工程目录结构

```text
stm32mp157-gateway/
├── m4/
│   └── Gateway/
│       ├── CM4/
│       │   ├── Core/Src/main.c          # M4 侧业务入口、RPMsg JSON 打包、LoRa 接入点
│       │   ├── OPENAMP/                 # M4 OpenAMP/RPMsg 支撑代码
│       │   ├── Drivers/
│       │   └── Middlewares/
│       ├── CA7/DeviceTree/              # A7 侧设备树相关工程内容
│       ├── Common/
│       ├── Drivers/
│       ├── Middlewares/
│       └── Gateway.ioc                  # STM32CubeIDE 工程配置
├── a7/
│   └── rpmsg_bridge.py                  # A7 侧 RPMsg 桥接、data.json 写入、RingBuffer 缓存
├── software/
│   └── PSRT_app/
│       ├── backend/
│       │   ├── app.py                   # Flask 后端入口，提供 API 并托管前端静态文件
│       │   ├── data.json                # 后端本地数据文件
│       │   └── requirements.txt
│       ├── frontend/
│       │   ├── public/
│       │   └── src/                     # React 前端源码
│       └── docs/                        # 原 PSRT_app 相关说明文档
├── board-files/
│   ├── home-root/
│   │   ├── a7/rpmsg_bridge.py           # 部署到板端 /home/root/a7/ 的桥接脚本副本
│   │   ├── M4_hw/                       # 板端 M4 固件启动/停止脚本
│   │   └── PSRT_app/                    # 部署到板端 /home/root/PSRT_app/ 的应用副本
│   ├── etc-systemd/systemd/system/
│   │   ├── start_m4.service             # M4 固件启动服务
│   │   └── a7-rpmsg-bridge.service      # A7 RPMsg 桥接服务
│   └── lib-firmware/firmware/           # 板端 /lib/firmware 固件镜像备份
├── .vscode/c_cpp_properties.json        # VSCode 打开工程根目录时的 C/C++ IntelliSense 配置
└── STM32MP157_GATEWAY_PROJECT.md        # 当前项目总说明文档
```

说明：

- `m4/Gateway/CM4/Core/Src/main.c` 是后续接入 LoRa UART 真实传感器数据的核心位置。
- `a7/rpmsg_bridge.py` 是 A7 侧业务桥接主程序，负责把 M4 数据落到本地展示文件和 RingBuffer 缓存。
- `board-files/` 下内容用于同步到 STM32MP157 板端，对应 `/home/root/`、`/etc/systemd/` 和 `/lib/firmware/` 等目录。
- `software/PSRT_app/` 是原 Web 应用源码，最终在板端以 Flask 后端加 React 静态前端形式运行。

## 4. M4 到 A7 的数据协议

M4 通过 RPMsg 发送一行 JSON，末尾使用 `\n` 分隔。当前业务字段示例：

```json
{"seq":1,"tick_ms":1000,"member_id":"1","heart_rate":72,"temperature":36.5,"latitude":39.9042,"longitude":116.4074}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `seq` | M4 发送序号，每发送一条递增，用于判断丢包或乱序 |
| `tick_ms` | M4 侧 `HAL_GetTick()` 时间，用于表示采集发生在 M4 启动后的毫秒数 |
| `member_id` | 队员 ID，与前端/后端队员信息对应 |
| `heart_rate` | 心率，单位 BPM |
| `temperature` | 体温，单位 °C |
| `latitude` | 纬度 |
| `longitude` | 经度 |

A7 侧会在写入缓存和 `data.json` 时补充 Linux 系统侧 ISO 时间戳。

## 5. RPMsg TTY 通道

M4 侧 RPMsg endpoint 名称必须为：

```c
#define RPMSG_SERVICE_NAME "rpmsg-tty-channel"
```

该名称用于匹配 A7 Linux 内核中的 `rpmsg_tty` 驱动。匹配成功后，A7 侧会生成：

```text
/dev/ttyRPMSG0
```

A7 可以通过该设备读取 M4 数据，也可以向 M4 下发控制命令。

## 6. A7 到 M4 的控制命令

A7 可以通过 `/dev/ttyRPMSG0` 向 M4 下发以下命令：

| 命令 | 作用 |
|---|---|
| `EN=0` | 关闭 M4 周期采集发送 |
| `EN=1` | 开启 M4 周期采集发送 |
| `PERIOD=<ms>` | 设置 M4 采集周期，单位毫秒 |

示例：

```sh
echo 'PERIOD=1000' >/dev/ttyRPMSG0
```

```sh
echo 'EN=1' >/dev/ttyRPMSG0
```

## 7. A7 桥接服务

A7 端桥接脚本路径：

```text
/home/root/a7/rpmsg_bridge.py
```

源码镜像路径：

```text
a7/rpmsg_bridge.py
board-files/home-root/a7/rpmsg_bridge.py
```

默认运行行为：

```text
读取 /dev/ttyRPMSG0
解析 M4 JSON Lines
写入 /home/root/PSRT_app/backend/data.json
写入 /home/root/PSRT_app/backend/rpmsg_cache.jsonl
```

手动运行：

```sh
python3 /home/root/a7/rpmsg_bridge.py
```

常用参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--device` | `/dev/ttyRPMSG0` | RPMsg TTY 设备 |
| `--data` | `/home/root/PSRT_app/backend/data.json` | Flask 后端数据文件 |
| `--cache` | `/home/root/PSRT_app/backend/rpmsg_cache.jsonl` | 持久化缓存文件 |
| `--cache-max-lines` | `10000` | 缓存最大行数 |
| `--cache-max-bytes` | `5242880` | 缓存最大字节数 |
| `--upload-url` | 空 | 远端上传地址，未配置时不联网 |

## 8. 持久化环形缓冲区

A7 侧使用 JSONL 文件实现应用层持久化 RingBuffer：

```text
/home/root/PSRT_app/backend/rpmsg_cache.jsonl
```

每一行是一条缓存记录，包含：

- `cache_id`
- `stored_at`
- `member_id`
- `seq`
- `tick_ms`
- `raw`
- `records`
- `uploaded`
- `attempts`
- `last_error`

缓存策略：

- 网络上传地址未配置时，数据只写入本地缓存。
- 配置 `--upload-url` 后，服务会优先上传缓存中的旧数据。
- 上传成功后删除对应缓存记录。
- 上传失败时保留缓存，并记录失败次数和错误信息。
- 缓存超过容量后保留最新记录，丢弃最旧记录，并输出 `cache overflow` 日志。

该机制能在配置容量范围内保证网络中断期间数据不丢失；如果离线时间过长导致缓存溢出，则会按环形缓冲策略覆盖旧数据。

## 9. 后端与前端

Flask 后端运行端口：

```text
5005
```

本地 API 示例：

```text
http://127.0.0.1:5005/api/data
```

局域网访问示例：

```text
http://192.168.137.150:5005/api/data
```

前端页面：

```text
http://192.168.137.150:5005
```

当前已经验证前端可以显示 M4 发送的业务数据，例如心率、体温和位置信息。

## 10. systemd 服务

当前涉及两个核心 systemd 服务：

```text
start_m4.service
```

用于启动 M4 固件。

```text
a7-rpmsg-bridge.service
```

用于启动 A7 RPMsg 桥接服务。

常用命令：

```sh
systemctl status start_m4.service a7-rpmsg-bridge.service
```

```sh
journalctl -u a7-rpmsg-bridge.service -f
```

```sh
systemctl restart start_m4.service
```

```sh
systemctl restart a7-rpmsg-bridge.service
```

## 11. 当前完成情况

已完成：

- M4 OpenAMP/RPMsg endpoint 创建。
- A7 `rpmsg_tty` 绑定并生成 `/dev/ttyRPMSG0`。
- M4 向 A7 发送 JSON Lines。
- A7 解析 JSON 并写入 `data.json`。
- A7 写入持久化 RingBuffer 缓存。
- Flask API 可读取数据。
- React 前端可显示数据。
- systemd 管理 M4 启动和 A7 桥接服务。

## 12. 后续工作

后续主要工作集中在真实传感器链路接入：

1. 在 M4 的 `Read_LoRa_SensorData()` 中接入 UART LoRa 接收逻辑。
2. 定义外部终端通过 LoRa 发送的数据帧格式。
3. 增加 UART 接收缓冲、帧解析和校验。
4. 将解析结果写入 `SensorData_t`。
5. 保持 M4 → A7 JSON 字段不变，减少 A7 和前端改动。
6. 明确真实远端服务器地址后，配置 `--upload-url` 并验证断网补传。

## 13. 关键文件

```text
m4/Gateway/CM4/Core/Src/main.c
```

M4 侧 RPMsg、JSON 打包和未来 LoRa UART 接入点。

```text
a7/rpmsg_bridge.py
```

A7 侧 RPMsg 数据桥接、data.json 写入、RingBuffer 缓存和上传钩子。

```text
board-files/home-root/a7/rpmsg_bridge.py
```

板端部署副本。

```text
board-files/etc-systemd/systemd/system/start_m4.service
```

M4 固件启动服务。

```text
board-files/etc-systemd/systemd/system/a7-rpmsg-bridge.service
```

A7 桥接服务。

```text
software/PSRT_app/backend/app.py
```

Flask 后端入口。

```text
software/PSRT_app/frontend/src
```

React 前端源码。
