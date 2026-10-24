"""Bounded Ogg/Opus parsing and firmware-format validation (no hardware required)."""
import struct
import unicodedata


class ToolError(Exception):
    """An actionable error which is safe to show to the user."""


def normalize_text(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    result = []
    for i, char in enumerate(text):
        numeric_sign = char in ".-+" and i + 1 < len(text) and text[i + 1].isdigit()
        if numeric_sign or not (char.isspace() or unicodedata.category(char).startswith("P")):
            result.append(char)
    return "".join(result)


def ogg_crc(data):
    value = 0
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
    return value


def opus_samples(packet):
    if not packet:
        raise ToolError("收到空的 Opus 音频帧。")
    config, code = packet[0] >> 3, packet[0] & 3
    samples = ((120, 240, 480, 960)[config & 3] if config >= 16 else
               (480, 960)[config & 1] if config >= 12 else
               (480, 960, 1920, 2880)[config & 3])
    if code == 3 and len(packet) < 2:
        raise ToolError("Opus 音频帧不完整。")
    count = 1 if code == 0 else 2 if code in (1, 2) else packet[1] & 63
    if not count or samples * count > 5760:
        raise ToolError("Opus 帧时长无效。")
    return samples * count


def read_ogg(data):
    if len(data) > 2 * 1024 * 1024:
        raise ToolError("音频文件过大。")
    offset, expected, stream = 0, 0, None
    pending, packets = bytearray(), []
    eos = False
    while offset < len(data):
        if eos or len(data) - offset < 27 or data[offset:offset + 4] != b"OggS":
            raise ToolError("OGG 文件不完整或格式错误。")
        header = data[offset:offset + 27]
        flags, serial, sequence = header[5], struct.unpack_from("<I", header, 14)[0], struct.unpack_from("<I", header, 18)[0]
        count = header[26]
        if header[4] != 0 or sequence != expected or (stream is not None and serial != stream):
            raise ToolError("OGG 页面顺序错误。")
        if bool(flags & 1) != bool(pending) or (expected == 0 and not flags & 2):
            raise ToolError("OGG 分包结构错误。")
        lacing = data[offset + 27:offset + 27 + count]
        end = offset + 27 + count + sum(lacing)
        if len(lacing) != count or end > len(data):
            raise ToolError("OGG 文件被截断。")
        page = bytearray(data[offset:end])
        checksum = struct.unpack_from("<I", page, 22)[0]
        page[22:26] = b"\0" * 4
        if ogg_crc(page) != checksum:
            raise ToolError("OGG 校验失败，文件可能损坏。")
        pos = offset + 27 + count
        for size in lacing:
            pending.extend(data[pos:pos + size])
            pos += size
            if len(pending) > 65536:
                raise ToolError("OGG 音频帧过大。")
            if size < 255:
                packets.append(bytes(pending))
                pending.clear()
        stream, expected, offset, eos = serial, expected + 1, end, bool(flags & 4)
    if pending or not eos or len(packets) < 3 or not packets[0].startswith(b"OpusHead") or not packets[1].startswith(b"OpusTags"):
        raise ToolError("需要完整的 OGG/Opus 音频。")
    return packets


def make_ogg(packets, sample_rate):
    if not packets or len(packets) > 512 or sum(map(len, packets)) > 128 * 1024:
        raise ToolError("服务端返回的语音过长或为空。")
    head = b"OpusHead" + struct.pack("<BBHIhB", 1, 1, 0, sample_rate, 0, 0)
    vendor = b"XiaoZhi current role"
    tags = b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", 0)
    output, granule = bytearray(), 0
    for index, payload in enumerate([head, tags] + packets):
        if index >= 2:
            granule += opus_samples(payload)
        lacing = bytes([255] * (len(payload) // 255) + [len(payload) % 255])
        flags = 2 if index == 0 else 4 if index == len(packets) + 1 else 0
        page = bytearray(b"OggS" + bytes([0, flags]) + struct.pack("<QIII", granule, 1, index, 0)
                         + bytes([len(lacing)]) + lacing + payload)
        page[22:26] = struct.pack("<I", ogg_crc(page))
        output.extend(page)
    return bytes(output)


def validate_firmware_ogg(data):
    packets = read_ogg(data)
    head, audio = packets[0], packets[2:]
    if len(head) < 19 or head[9] != 1 or struct.unpack_from("<I", head, 12)[0] != 16000 or head[18] != 0:
        raise ToolError("固件需要单声道、16kHz、mapping 0 的 Opus。")
    if any(opus_samples(packet) != 2880 for packet in audio):
        raise ToolError("固件提示语需要每帧 60ms。")
    seconds = len(audio) * .06
    if seconds > 8 or len(audio) > 160 or sum(map(len, audio)) > 65536:
        raise ToolError(f"生成的提示语长约 {seconds:.2f} 秒，超过固件 8 秒限制，请缩短文字。")
    return {"sample_rate": 16000, "channels": 1, "frame_ms": 60, "frames": len(audio), "seconds": round(seconds, 2)}
