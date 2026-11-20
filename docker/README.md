# 飞牛 Docker Compose 部署

ESP32 通过 USB 串口发送内置页面和实时画面，并保存灯阵设置。容器将网页请求转发到串口，同时采集飞牛 CPU、网速和存储卷占用率。ESP32 无需连接 Wi-Fi。

## 使用发布镜像

在飞牛上获取本仓库，进入 `docker` 目录，复制并填写 `.env`。

```sh
cd docker
cp .env.example .env
ls -l /dev/serial/by-id/
stat -Lc '%g' /dev/ttyUSB0
```

- `NAS_IP` 填飞牛的局域网 IPv4 地址，`NAS_PORT` 默认为 `8765`。
- `SERIAL_DEVICE` 填实际设备。建议使用 `/dev/serial/by-id/` 下的稳定路径，`SERIAL_GID` 填串口所属组的数字 ID。
- `NAS_VOLUME` 填要监测的存储卷，例如 `/vol1`，容器以只读方式挂载。

```sh
sudo docker compose -f compose.image.yaml up -d
sudo docker compose -f compose.image.yaml ps
```

打开 `http://飞牛局域网IP:8765`，或使用飞牛 Docker 容器列表中的「快捷访问」。更改 `NAS_PORT` 时，请使用对应端口。镜像发布在 `ghcr.io/wwwwwwjy/fnos-life-matrix`，运行本项目需要已烧录 USB 版固件的 ESP32。

## 飞牛本地离线构建

如果飞牛无法访问公共镜像仓库，可在仓库根目录用飞牛现有的 Python 构建镜像。该路径在飞牛的 x86-64、Python 3.11 环境实测。

```sh
python3 docker/prepare_runtime.py
cd docker
sudo docker compose up -d --build --pull never
sudo docker compose logs --tail=30
```

飞牛系统或 Python 更新后，重新生成运行环境再重建容器。重新插拔设备后，若设备节点发生变化，可用 Compose 重新创建容器以刷新设备映射。
