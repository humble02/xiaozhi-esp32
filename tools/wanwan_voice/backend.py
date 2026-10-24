"""Generate prompts through the user's existing SparkBot role, without writing flash."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import queue
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from zlib import crc32

from audio_format import ToolError, make_ogg, normalize_text, read_ogg, validate_firmware_ogg


class Cancelled(ToolError):
    pass


class Retryable(ToolError):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("已取消生成。")


def delay(seconds, cancel):
    if cancel.wait(seconds):
        raise Cancelled("已取消生成。")


def device_ports():
    from serial.tools.list_ports import comports
    # This build controls the SparkBot's native ESP32-S3 USB port only.
    return [(p.device, p.description) for p in comports() if p.vid == 0x303A and p.pid == 0x1001]


def parse_mqtt_settings(backup):
    if len(backup) < 0x10000:
        raise ToolError("设备配置读取不完整。")
    nvs = None
    for offset in range(0x8000, 0x8C00, 32):
        entry = backup[offset:offset + 32]
        if entry[:2] != b"\xaa\x50":
            break
        name = entry[12:28].split(b"\0", 1)[0]
        if name == b"nvs" and entry[2:4] == b"\x01\x02":
            start, size = struct.unpack_from("<II", entry, 4)
            if start % 4096 or size % 4096 or start + size > len(backup):
                raise ToolError("设备 NVS 分区不在支持的配置区内。")
            nvs = backup[start:start + size]
    if nvs is None:
        raise ToolError("未找到当前 SparkBot 的 NVS 配置分区。")
    pages = [nvs[i:i + 4096] for i in range(0, len(nvs), 4096)]
    pages = sorted((p for p in pages if p[:4] in (b"\xfe\xff\xff\xff", b"\xfc\xff\xff\xff")),
                   key=lambda p: struct.unpack_from("<I", p, 4)[0])
    namespaces, settings = {}, {}
    for page in pages:
        if crc32(page[4:28], 0xFFFFFFFF) != struct.unpack_from("<I", page, 28)[0]:
            raise ToolError("配置页校验失败，请重试读取设备。")
        index = 0
        while index < 126:
            pos = 64 + index * 32
            entry = page[pos:pos + 32]
            span = entry[2] if entry[2] not in (0, 255) else 1
            state = (page[32 + index // 4] >> ((index % 4) * 2)) & 3
            if state == 2:
                if index + span > 126 or crc32(entry[:4] + entry[8:], 0xFFFFFFFF) != struct.unpack_from("<I", entry, 4)[0]:
                    raise ToolError("配置条目校验失败。")
                name = entry[8:24].split(b"\0", 1)[0].decode("ascii", errors="strict")
                if entry[0] == 0 and entry[1] == 1:
                    namespaces[entry[24]] = name
                elif namespaces.get(entry[0]) == "mqtt" and entry[1] == 0x21:
                    size = struct.unpack_from("<H", entry, 24)[0]
                    if not 0 < size <= (span - 1) * 32:
                        raise ToolError("设备服务配置长度错误。")
                    data = page[pos + 32:pos + 32 + size]
                    if crc32(data, 0xFFFFFFFF) != struct.unpack_from("<I", entry, 28)[0]:
                        raise ToolError("设备服务配置校验失败。")
                    settings[name] = data.rstrip(b"\0").decode("utf-8")
            index += span
    required = ("endpoint", "client_id", "username", "password", "publish_topic")
    if not all(settings.get(k) for k in required):
        raise ToolError("设备尚未保存小智连接信息，请先完成联网和激活。")
    if settings["endpoint"] not in ("api.tenclass.net", "api.tenclass.net:8883"):
        raise ToolError("这个版本仅支持当前小智官方服务端 api.tenclass.net。")
    if any(len(settings[k].encode("utf-8")) > 4096 for k in required):
        raise ToolError("设备连接配置过长。")
    return settings


def read_device_config(port):
    import esptool
    from esptool.targets.esp32s3 import ESP32S3ROM
    esp = None
    # esptool's output contains hardware details; keep it out of exported files.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            esp = ESP32S3ROM(port, 115200)
            esp.connect(mode="usb_reset", attempts=3)
            esp = esp.run_stub()
            esp.change_baud(460800)
            esp.flash_set_parameters(16 * 1024 * 1024)
            return parse_mqtt_settings(esp.read_flash(0, 0x20000))
        except ToolError:
            raise
        except (esptool.FatalError, OSError) as error:
            raise ToolError("无法读取 SparkBot。请确认 USB 已连接、串口未被占用，再重试。") from error
        finally:
            if esp is not None:
                esp._port.close()


def restore_device(port):
    import serial
    from esptool.reset import HardReset
    connection = serial.Serial()
    connection.port, connection.baudrate, connection.timeout = port, 115200, .2
    connection.dtr = connection.rts = False
    try:
        connection.open()
        HardReset(connection, uses_usb=True)()
    finally:
        connection.close()


@contextmanager
def paused_device(port, report, cancel):
    if port not in dict(device_ports()):
        raise ToolError("未找到所选 SparkBot 原生 USB 串口，请连接设备后刷新。")
    check_cancel(cancel)
    report("正在读取设备连接信息，临时暂停设备……")
    try:
        settings = read_device_config(port)
        check_cancel(cancel)
        yield settings
    finally:
        failure_in_progress = sys.exc_info()[0] is not None
        report("正在恢复 SparkBot 运行……")
        try:
            restore_device(port)
        except Exception as error:
            report("设备恢复失败，请拔插 USB 让设备重新启动。")
            if not failure_in_progress:
                raise ToolError("设备恢复失败，请拔插 USB 后重试。") from error


def run_process(command):
    try:
        result = subprocess.run(command, capture_output=True, timeout=40,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolError("音频转换程序无法运行或超时。") from error
    if result.returncode:
        raise ToolError("音频转换失败，请重试。")


def speak_request(text, destination):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    stream = None
    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voices = voice.GetVoices()
        chinese = [voices.Item(i) for i in range(voices.Count) if "Huihui" in voices.Item(i).GetDescription()]
        if not chinese:
            raise ToolError("Windows 缺少“Microsoft Huihui”中文语音，请安装简体中文语音包。")
        voice.Voice, voice.Rate, voice.Volume = chinese[0], -1, 100
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format.Type = 22
        stream.Open(str(destination), 3, False)
        voice.AudioOutputStream = stream
        voice.Speak(text)
    except ToolError:
        raise
    except Exception as error:
        raise ToolError("无法生成朗读指令，请检查 Windows 中文语音组件。") from error
    finally:
        if stream is not None:
            stream.Close()
        pythoncom.CoUninitialize()


def request_audio(text, attempt, directory):
    import imageio_ffmpeg
    phrase = text.replace("啊", "，啊，") if attempt == 1 else text
    instruction = (f"请你只说这一句话，{phrase}，不要说其他内容。" if attempt < 2 else
                   f"请只朗读下面这句话，{phrase}，读完就停，不要加其他话。")
    source, encoded = directory / "request.wav", directory / "request.ogg"
    speak_request(instruction, source)
    run_process([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(source),
                 "-af", "adelay=250,apad=pad_dur=0.3", "-ac", "1", "-ar", "16000", "-c:a", "libopus",
                 "-application", "voip", "-b:a", "20k", "-frame_duration", "60", str(encoded)])
    return read_ogg(encoded.read_bytes())[2:]


def capture_phrase(settings, packets, report, cancel):
    import paho.mqtt.client as mqtt
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    incoming, datagrams = queue.Queue(maxsize=512), queue.Queue(maxsize=512)
    connected, stopped = threading.Event(), threading.Event()
    connection_ok, receiver_errors = [], []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings["client_id"],
                         clean_session=True, reconnect_on_failure=False)
    client.username_pw_set(settings["username"], settings["password"])
    client.tls_set_context(ssl.create_default_context())
    udp, receiver, session, loop_started = None, None, None, False

    def on_connect(c, userdata, flags, reason, properties):
        connection_ok.append(not reason.is_failure)
        if not reason.is_failure and settings.get("subscribe_topic"):
            c.subscribe(settings["subscribe_topic"])
        connected.set()

    def on_message(c, userdata, message):
        try:
            incoming.put_nowait(json.loads(message.payload))
        except (ValueError, UnicodeError, queue.Full):
            pass

    def send(message):
        info = client.publish(settings["publish_topic"], json.dumps(message, ensure_ascii=False))
        info.wait_for_publish(timeout=5)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise Retryable("服务端消息发送失败。")

    client.on_connect, client.on_message = on_connect, on_message
    try:
        client.connect("api.tenclass.net", 8883, 60)
        client.loop_start()
        loop_started = True
        deadline = time.monotonic() + 15
        while not connected.wait(.1):
            check_cancel(cancel)
            if time.monotonic() > deadline:
                raise Retryable("连接小智服务端超时，请检查电脑网络。")
        if not connection_ok[0]:
            raise ToolError("设备连接信息已失效，请先重新启动并联网激活 SparkBot。")
        send({"type": "hello", "version": 3, "transport": "udp", "features": {},
              "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}})
        hello, deadline = None, time.monotonic() + 15
        while time.monotonic() < deadline:
            check_cancel(cancel)
            try:
                message = incoming.get(timeout=.1)
            except queue.Empty:
                continue
            if message.get("type") == "hello":
                hello = message
                break
        if not hello or hello.get("transport") != "udp":
            raise Retryable("未收到小智语音通道，请稍后重试。")
        session = hello["session_id"]
        params = hello["udp"]
        key, nonce = bytes.fromhex(params["key"]), bytearray.fromhex(params["nonce"])
        if len(key) != 16 or len(nonce) != 16 or hello["audio_params"].get("channels", 1) != 1:
            raise ToolError("服务端返回了不支持的音频配置。")
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.settimeout(.1)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        udp.connect((params["server"], int(params["port"])))

        def receive():
            while not stopped.is_set():
                try:
                    datagrams.put_nowait(udp.recv(65535))
                except socket.timeout:
                    continue
                except Exception:
                    receiver_errors.append(True)
                    return

        receiver = threading.Thread(target=receive, daemon=True)
        receiver.start()

        def send_audio(payload, sequence, timestamp):
            nonce[2:4], nonce[8:12], nonce[12:16] = struct.pack("!H", len(payload)), struct.pack("!I", timestamp), struct.pack("!I", sequence)
            encryptor = Cipher(algorithms.AES(key), modes.CTR(bytes(nonce))).encryptor()
            udp.send(bytes(nonce) + encryptor.update(payload) + encryptor.finalize())

        send_audio(b"\xf8\xff\xfe", 1, 0)
        send({"session_id": session, "type": "listen", "state": "start", "mode": "manual"})
        report("正在向当前小智角色发送朗读请求……")
        for sequence, payload in enumerate(packets, 2):
            check_cancel(cancel)
            send_audio(payload, sequence, (sequence - 2) * 60)
            delay(.06, cancel)
        send({"session_id": session, "type": "listen", "state": "stop"})
        report("正在接收“湾湾小何”语音……")
        audio, sentences, stop_at = {}, [], None
        deadline, last_audio = time.monotonic() + 45, time.monotonic()
        while time.monotonic() < deadline:
            check_cancel(cancel)
            while not incoming.empty():
                message = incoming.get_nowait()
                if message.get("type") == "tts":
                    if message.get("state") == "sentence_start":
                        sentences.append(message.get("text", ""))
                        report("返回文字：" + sentences[-1])
                    elif message.get("state") == "stop":
                        stop_at = time.monotonic()
                elif message.get("type") == "alert":
                    raise Retryable("服务端暂时无法生成语音，请稍后重试。")
            try:
                data = datagrams.get(timeout=.1)
            except queue.Empty:
                data = b""
            if data:
                if len(data) < 16 or data[0] != 1 or struct.unpack("!H", data[2:4])[0] != len(data) - 16:
                    raise Retryable("接收到不完整的音频包，请重试。")
                sequence = struct.unpack("!I", data[12:16])[0]
                decryptor = Cipher(algorithms.AES(key), modes.CTR(data[:16])).decryptor()
                audio[sequence] = decryptor.update(data[16:]) + decryptor.finalize()
                last_audio = time.monotonic()
                if len(audio) > 256 or sum(map(len, audio.values())) > 128 * 1024:
                    raise ToolError("语音过长，请缩短文字后重试。")
            if stop_at and time.monotonic() - max(stop_at, last_audio) > .8:
                break
        ordered = sorted(audio)
        if not stop_at or not ordered:
            raise Retryable("没有收到完整语音，请稍后重试。")
        if receiver_errors or ordered != list(range(1, ordered[-1] + 1)):
            error = Retryable("音频网络传输丢包，正在重试。")
            error.audio_metrics = {"first": ordered[0], "last": ordered[-1], "count": len(ordered),
                                   "receiver_failed": bool(receiver_errors)}
            raise error
        return make_ogg([audio[i] for i in ordered], hello["audio_params"]["sample_rate"]), "".join(sentences)
    except (OSError, TimeoutError) as error:
        raise Retryable("网络连接失败，请确认电脑可访问小智服务端。") from error
    finally:
        if session and client.is_connected():
            try:
                send({"type": "abort", "session_id": session})
                send({"type": "goodbye", "session_id": session})
            except Exception:
                pass
        stopped.set()
        if receiver:
            receiver.join(timeout=1)
        if udp:
            udp.close()
        client.disconnect()
        if loop_started:
            client.loop_stop()


def generate(text, port, output_directory, stem, report, cancel):
    text = text.strip()
    if not normalize_text(text) or len(text) > 100 or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise ToolError("请输入 1–100 字的有效文字；固件提示语建议控制在约 7 秒以内。")
    if not re.fullmatch(r"[\w-]{1,64}", stem) or stem.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
        raise ToolError("文件名请使用文字、数字、下划线或短横线。")
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    import imageio_ffmpeg
    check_cancel(cancel)
    with tempfile.TemporaryDirectory(prefix="wanwan_voice_") as scratch:
        directory = Path(scratch)
        with paused_device(port, report, cancel) as settings:
            matched, last_error = False, ""
            for attempt in range(3):
                check_cancel(cancel)
                report(f"第 {attempt + 1}/3 次：准备朗读请求……")
                packets = request_audio(text, attempt, directory)
                check_cancel(cancel)
                try:
                    raw, returned = capture_phrase(settings, packets, report, cancel)
                    if normalize_text(returned) != normalize_text(text):
                        raise Retryable("返回文字与输入不同，这段音频未导出。")
                    matched = True
                    break
                except Retryable as error:
                    last_error = str(error)
                    report(last_error)
                    if attempt < 2:
                        report("等待 6 秒后重新生成……")
                        delay(6, cancel)
            if not matched:
                raise ToolError(f"三次生成未通过核对。{last_error} 请重新生成或把句子写得更简短。")
        check_cancel(cancel)
        report("文字核对通过，正在转换为固件 OGG/Opus……")
        source, encoded, preview = directory / "source.ogg", directory / "prompt.ogg", directory / "preview.wav"
        source.write_bytes(raw)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        run_process([ffmpeg, "-loglevel", "error", "-y", "-i", str(source), "-af", "apad=pad_dur=0.25",
                     "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "20k", "-application", "voip",
                     "-frame_duration", "60", str(encoded)])
        details = validate_firmware_ogg(encoded.read_bytes())
        run_process([ffmpeg, "-loglevel", "error", "-y", "-i", str(encoded), "-ar", "16000", "-ac", "1",
                     "-c:a", "pcm_s16le", str(preview)])
        with wave.open(str(preview), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getnframes() == 0:
                raise ToolError("试听文件验证失败。")
        check_cancel(cancel)
        name = stem + "_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        final = output_directory / (name + ".ogg")
        record = {"text": text, "server_text": returned, "text_matches_ignoring_punctuation": True,
                  "voice": "设备当前角色（请保持湾湾小何）", "firmware_compatible": True,
                  "sha256": hashlib.sha256(encoded.read_bytes()).hexdigest(), **details}
        # Each export has a unique name; existing prompts are never overwritten.
        with final.open("xb") as output:
            output.write(encoded.read_bytes())
        with final.with_suffix(".wav").open("xb") as output:
            output.write(preview.read_bytes())
        with final.with_suffix(".json").open("x", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return {"ogg": str(final), "wav": str(final.with_suffix(".wav")), **record}
