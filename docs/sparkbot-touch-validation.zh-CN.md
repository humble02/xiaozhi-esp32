# SparkBot 触摸对话版验证记录

日期：2026-10-24。已验证的代码提交：`c8dc5e8cf9ae4801972eea9cbd4fab2b660fea6c`。之后的交付文档提交不改变固件代码。

[GitHub Actions 完整记录](https://github.com/humble02/xiaozhi-esp32/actions/runs/40071510079)

| 检查 | 结果 |
| --- | --- |
| Linux 完整主机测试 | 148 项通过，无跳过项 |
| 原版 `esp-sparkbot` | ESP-IDF `release-v6.1` 完整编译、合并固件和打包成功 |
| 新版 `esp-sparkbot-touch` | ESP-IDF `release-v6.1` 完整编译、合并固件和打包成功 |
| Windows 新增 C++ 行为测试 | 2 项通过 |
| CMake 依赖与源码选择 | S3 配置尚未加载、S3 关闭/开启触摸、C3、P4 共 5 个场景通过 |
| 补丁及格式 | `git diff --check` 通过；新建 3 个 C++ 文件的 clang-format 检查通过，已有文件仅格式化修改区域 |
| 触摸版固件下载 | 压缩包 CRC 校验通过，解压后的文件 SHA256 核对通过 |

主机行为测试执行实际触摸防抖代码和应用会话处理函数，覆盖噪声、长按、松开再按、快速重复触碰、待机开始、聆听/播报中停止、连接成功/失败期间取消、停止后的迟到 TTS，以及启动/配网/激活/升级状态忽略触摸。传输测试用模拟的即时、延迟关闭回调覆盖两类关闭时序，不能替代真实 WebSocket、MQTT/UDP 设备联调。

原生触摸驱动的依赖按 `IDF_TARGET` 声明，以便 ESP-IDF 在尚未加载 sdkconfig 的早期阶段发现它；触摸处理源码是否编译仍由触摸功能开关控制。

Windows 下完整旧有测试集受 Bash、编译器及可选开发依赖影响，未全部通过；完整主机测试以本次 Linux CI 的 148 项成功记录为准。本地昨天的 ESP-IDF 5.2.3 环境未用于编译当前上游，也未被改动。

## 固件文件

本地工作区同级目录 `xiaozhi-sparkbot-touch-firmware-20261024` 保存触摸版固件及说明。源自本次工作流的 `esp-sparkbot-touch-firmware` artifact。

- 合并固件文件：`merged-binary.bin`。
- 大小：10052777 字节。
- SHA256：`91dd0f3e7c333bb4e7b59e4edc046ced7695e9f525384c57a7a6fb4055c6e065`。

## 实物状态

本次没有连接串口、刷写或擦除设备。原项目和原设备备份保留。

待验证：顶部灵敏度、长按与误触、真实录音及播报、结束后保持待机、断网恢复、原有语音唤醒、摄像头/底盘，以及接入真实后端天气工具后的城市与更新时间。完整步骤见 [板级说明](../main/boards/espressif/esp-sparkbot/README.md)。
