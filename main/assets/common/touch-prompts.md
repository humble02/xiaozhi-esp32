# 顶部触摸对话提示音

- `touch_greeting.ogg`：您好，找我有什么事情吗？
- `touch_farewell.ogg`：再见啊，下次有需求可以再来找我

2026-10-24 使用 Windows 本机 Microsoft Huihui Desktop 简体中文音色离线合成，语速 -1、音量 100，再转成单声道 16kHz Opus OGG。设备运行时直接播放，无需 TTS API、网络或模型生成这两句话。

转码参数：`-ac 1 -ar 16000 -c:a libopus -b:a 20k -application voip -frame_duration 60`。

原始 WAV 时长约为 3.79 秒和 5.05 秒。OGG 帧为 60ms，播放可能包含不足一帧的结尾填充。提示语文本与语音文件必须一起修改。
