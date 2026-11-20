# 飞牛生命灯阵 · fnOS Life Matrix

用 ESP32 和 MAX7219 四联点阵运行康威生命游戏，把飞牛 NAS 的 CPU 占用、网络收发速率和存储卷占用率映射到灯光。ESP32 通过一根 USB 数据线连接飞牛。浏览器通过局域网访问 Docker 提供的入口，无需给 ESP32 配置 Wi-Fi。

- **实时预览**：网页显示 ESP32 回传的 32×8 点阵，可用固定图案校准每块灯板方向和排列顺序。
- **独立开关**：网速调动画速度，存储卷占用率调生命代数上限，CPU 占用率调亮度。三个映射各自支持输入与输出上下限。
- **脱机运行**：关闭飞牛协同后，灯阵按 ESP32 保存的设置运行。开启协同和失联熄灯时，约 15 秒收不到 NAS 数据就会熄灯。若 NAS 同时切断 USB 电源，灯会立即熄灭。
- **设置保存在 ESP32**：Docker 负责提供局域网入口和采集飞牛数据。动画、网页资源和设置均由 ESP32 提供。

## 快速开始

1. 准备经典 ESP32 开发板、四联 MAX7219 32×8 点阵及支持数据的 USB 线，按下文接线。
2. 用 VS Code 的 PlatformIO 打开本项目，选择 `esp32dev` 环境并执行 **Upload**。也可以在命令行运行 `pio run -t upload --upload-port <串口>`。
3. 将 ESP32 插到飞牛，按 [Docker Compose 部署指南](docker/README.md) 配置串口和存储卷，启动容器。
4. 在飞牛 **Docker → 容器 → 快捷访问** 中选择端口，或在浏览器访问 `http://飞牛局域网IP:8765`。点「显示校准图案」核对实体与预览，再保存设置。

发布镜像为 `ghcr.io/wwwwwwjy/fnos-life-matrix:latest`，面向 Linux `amd64` 飞牛主机。[GitHub Container Registry](https://github.com/wwwwwwjy/fnos-life-matrix/pkgs/container/fnos-life-matrix)

## 接线

先断电。使用灯板背面印有 **DIN** 的一端，照片里就是带有 `VCC / GND / DIN / CS / CLK` 的外侧排针。

| 灯板 | 接到 |
| --- | --- |
| VCC | 稳定的 5 V 电源 |
| GND | ESP32 GND、电平转换器 GND 和 5 V 电源 GND 共地 |
| DIN | ESP32 `D23` 经 3.3 V→5 V 电平转换 |
| CS | ESP32 `D27` 经 3.3 V→5 V 电平转换 |
| CLK | ESP32 `D18` 经 3.3 V→5 V 电平转换 |

MAX7219 用 5 V 供电时，数据手册规定输入高电平至少为 3.5 V。可用 5 V 供电的 **74AHCT125** 转换这三路信号。将使用的通道的 `/OE` 接地，芯片电源旁接 100 nF 去耦电容。灯板 VCC 接 5 V，ESP32 继续用自己的 USB 供电。若想让一根 Type-C 线同时给 ESP32 和灯板供电，先确认这块开发板的 `VIN` 在 USB 供电时确实输出约 5 V，并确认供电能力足够。灯板使用独立 5 V 电源时，只与 ESP32 共地，避免把两路 5 V 直接并接。

## 使用说明

- 网速读取所选网卡的接收与发送速率之和，单位为 MB/s。留空时优先选择 IPv4 默认路由网卡。
- 存储卷使用 Compose 中配置的挂载路径。代数达到上限时重新播种，灭绝、静止及短周期循环也会触发重新播种。
- 亮度为 0–15 档，亮度 0 是最低亮度。关灯使用面板的电源开关。
- 修改设置会立即预览，点「保存设置」才会写入 ESP32。面板用于可信局域网，访问无需登录。
- 当前支持浏览器控制。米家 App 接入尚未实现。

## 致谢与许可证

感谢 [Lajos Hankó（@lajoshanko）](https://github.com/lajoshanko) 开源 [ESPHome MAX7219 Game of Life](https://github.com/lajoshanko/esphome_max7219_game_of_life)，它是本项目将生命游戏呈现在 MAX7219 点阵上的灵感来源。本项目加入了 Arduino/PlatformIO 固件、飞牛指标联动、USB 串口控制和网页实时预览。

本项目采用 [MIT 许可证](LICENSE)，保留原项目的版权声明。
