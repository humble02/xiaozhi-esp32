# 语音交互、天气查询与 SparkBot 触摸改造

核查日期：2026-10-24。结论来自指定仓库源码，未把 README 的能力列表当作本地实测结果。

## 参考范围

用户提供的 `yuyirobot` 是账号主页。本次实际比较：

- [yuyirobot/xiaozhi-esp32](https://github.com/yuyirobot/xiaozhi-esp32)，核查提交 `140aab8999f0d194fbc08f1dcdea56c6b53c0754`。
- [yuyirobot/xiaozhi-esp32-server](https://github.com/yuyirobot/xiaozhi-esp32-server)，核查提交 `d730a6cd06c6bc27d24bf4a93cb8a3b41f4de824`。
- [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)，本次修改基线 `39d23aee5b8682f77bbdf9cdc66799456b5ad408`。

`yuyirobot/esp-fireware` 在核查时仅有简短 README，没有可供分析的固件源码。上述两个小智仓库才提供相关实现。

## 语音交互怎样完成

设备侧的共同链路是：麦克风 → I2S/音频芯片 → 本地唤醒与语音前端 → Opus 音频压缩 → WebSocket 或 MQTT/UDP → 服务端。回复音频通过网络返回，解码后交给扬声器；识别文字和表情消息用于更新屏幕。

`yuyirobot/xiaozhi-esp32` 是较早的小智客户端实现。其服务端在 `core/connection.py`、`core/handle/receiveAudioHandle.py` 和 `core/providers/` 中分别组织连接、收音及模型适配。核查版本的默认模块是 SileroVAD、FunASR、ChatGLMLLM、EdgeTTS；这些是可配置适配器，不意味着必须使用某一家云服务。

上游现版把设备音频处理集中到 `main/audio/audio_service.cc`，把会话行为集中到 `main/application.cc`。离线唤醒主要用于检测唤醒词，完整问题的识别、模型回答和语音合成通常仍由服务端承担，也可由服务端接入实时语音模型。设备侧 MCP 负责提供设备工具，服务端/云端 MCP 可以扩展查询能力。

## 天气查询怎样完成

[`get_weather.py`](https://github.com/yuyirobot/xiaozhi-esp32-server/blob/d730a6cd06c6bc27d24bf4a93cb8a3b41f4de824/main/xiaozhi-server/plugins_func/functions/get_weather.py) 注册了 `get_weather` 函数工具：

1. 模型或意图模块从问话中提取地点；地点缺省时依次使用客户端 IP 城市、配置的默认城市。
2. 调用和风天气城市查找接口，取得城市信息与 `fxLink`。
3. 用 `requests` 访问 `fxLink` 天气网页，用 BeautifulSoup 提取当前天气、参数和最多 7 天预报。
4. 返回 `Action.REQLLM`，把真实查询到的数据交给模型整理成自然语言，再经 TTS 播放。

因此，这个版本并不是在 ESP32 固件里直接查询全部天气数据，也不是纯粹靠大模型猜天气。它使用“城市 API + 天气网页抓取”，并非全程调用结构化天气 API。网页 DOM、CSS 选择器或旧接口改变可能造成失败；源码请求未设置超时，部分网页解析也缺少空值保护。本次没有使用其公开配置中的任何凭据，也没有验证该旧天气服务目前仍可用。

`78/xiaozhi-esp32` 的通用固件没有上述 `get_weather` 实现。查询能力需要所连接后端的天气插件或 MCP 服务；仅克隆、编译客户端并不能保证天气查询已接通。[上游 README](https://github.com/78/xiaozhi-esp32/blob/39d23aee5b8682f77bbdf9cdc66799456b5ad408/README_zh.md) 和 `docs/mcp-protocol.md` 描述了设备与服务端的分工。

对当前研究终端，可沿用昨天工程“向真实天气接口取数、标明默认城市和更新时间”的做法，再通过服务端工具接入小智。后续若采用和风天气，应根据其当前官方接口配置，而不直接复制这个旧抓取实现。失败时明确告知查询失败，避免生成貌似实时的天气回答。

## 为什么触摸改造可行

上游已经有 `espressif/esp-sparkbot` 板级实现，屏幕、ES8311、摄像头和底盘接口可复用。本地出厂 BSP 的顶部确认电极映射为触摸通道 1；无需增加额外触摸模块。

原 `ToggleChatState()` 在播报时执行打断，在聆听时关闭音频通道；`StopListening()` 则主要表示提交一次手动录音。这两者都不能直接等同于“用户明确结束整个对话”。因此新增独立的 `ToggleConversation()`：待机时开始，其余会话状态停止收音、清空待播音频、发送停止/打断并关闭通道，同时拒绝停止后的迟到 TTS。

触摸任务只发送事件，应用状态统一在主任务修改。连接前和握手返回后都会检查取消请求；网络握手自身仍沿用上游同步等待机制，因此连接中的停止不是立即中断网络系统调用。关闭路径使用通用 `Protocol` 接口，适用于 WebSocket 和 MQTT/UDP。

顶部采用 [乐鑫官方新版触摸驱动](https://github.com/espressif/esp-idf/tree/release/v6.1/examples/peripherals/touch_sensor/touch_sens_basic)，加上稳定采样、迟滞和防重复处理。新增发布变体 `esp-sparkbot-touch`，保持原版可用；具体参数与实物验收步骤见 [板级说明](../main/boards/espressif/esp-sparkbot/README.md)。

## 验证边界

本地原开发环境为 ESP-IDF 5.2.3，当前小智最低要求 6.0.1；未用旧工具链冒充成功构建。仓库新增主机侧行为测试，并配置 GitHub Actions 独立编译原版与触摸版。实际检查结果以提交记录、Actions 和交付说明为准；主机测试或云端编译都不能替代实物验收。

本次交付已完成两种固件的云端编译，148 项主机测试全部通过。具体代码版本、校验值及未完成的实物测试见 [验证记录](sparkbot-touch-validation.zh-CN.md)。
