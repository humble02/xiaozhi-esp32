"""Format, configuration integrity, cancellation/recovery and export behavior."""
from contextlib import contextmanager
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from zlib import crc32

from audio_format import ToolError, make_ogg, normalize_text, read_ogg, validate_firmware_ogg
import backend

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "main/assets/common/touch_greeting.ogg"


def config_fixture(endpoint="api.tenclass.net"):
    data = bytearray(b"\xff" * 0x20000)
    table = struct.pack("<HBBII16sI", 0x50AA, 1, 2, 0x9000, 4096, b"nvs", 0)
    data[0x8000:0x8020] = table
    page = bytearray(b"\xff" * 4096)
    page[:4], page[4:8], page[8] = struct.pack("<I", 0xFFFFFFFE), struct.pack("<I", 0), 0xFE
    page[28:32] = struct.pack("<I", crc32(page[4:28], 0xFFFFFFFF))
    index = 0

    def add(namespace, kind, key, content):
        nonlocal index
        raw = content.encode() + b"\0" if kind == 0x21 else b""
        span = 1 + (len(raw) + 31) // 32
        entry = bytearray(b"\xff" * 32)
        entry[:4] = bytes([namespace, kind, span, 255])
        entry[8:24] = key.encode().ljust(16, b"\0")
        entry[24:32] = (struct.pack("<HHI", len(raw), 0xFFFF, crc32(raw, 0xFFFFFFFF)) if raw
                        else bytes([content]) + b"\xff" * 7)
        entry[4:8] = struct.pack("<I", crc32(entry[:4] + entry[8:], 0xFFFFFFFF))
        pos = 64 + index * 32
        page[pos:pos + 32] = entry
        if raw:
            page[pos + 32:pos + span * 32] = raw.ljust((span - 1) * 32, b"\xff")
        for i in range(index, index + span):
            page[32 + i // 4] &= ~(1 << ((i % 4) * 2))
        index += span

    add(0, 1, "mqtt", 7)
    for key, value in {"endpoint": endpoint, "client_id": "device-test", "username": "test-user",
                       "password": "not-a-real-password", "publish_topic": "test/up", "subscribe_topic": "test/down"}.items():
        add(7, 0x21, key, value)
    data[0x9000:0xA000] = page
    return bytes(data)


class ToolTests(unittest.TestCase):
    def test_real_firmware_assets_and_crc_rejection(self):
        for name in ("touch_greeting.ogg", "touch_farewell.ogg"):
            content = (FIXTURE.parent / name).read_bytes()
            details = validate_firmware_ogg(content)
            self.assertLessEqual(details["seconds"], 8)
            self.assertEqual(details["frame_ms"], 60)
            corrupted = bytearray(content)
            corrupted[-1] ^= 1
            for invalid in (content[:-1], bytes(corrupted), b"not audio", content + content):
                with self.assertRaises(ToolError):
                    validate_firmware_ogg(invalid)

    def test_ogg_roundtrip_and_duration_limit(self):
        packets = read_ogg(FIXTURE.read_bytes())[2:]
        self.assertEqual(read_ogg(make_ogg(packets, 16000))[2:], packets)
        with self.assertRaisesRegex(ToolError, "8 秒"):
            validate_firmware_ogg(make_ogg([packets[0]] * 134, 16000))
        with self.assertRaises(ToolError):
            validate_firmware_ogg(make_ogg(packets, 24000))

    def test_text_comparison_accepts_punctuation_but_preserves_content(self):
        self.assertEqual(normalize_text("再见啊，下次来找我"), normalize_text("再见啊，下次来找我。"))
        self.assertNotEqual(normalize_text("再见啊"), normalize_text("再见了"))
        self.assertNotEqual(normalize_text("1.2"), normalize_text("12"))
        self.assertNotEqual(normalize_text("-2"), normalize_text("2"))
        self.assertNotEqual(normalize_text("C++"), normalize_text("C"))

    def test_nvs_integrity_and_destination(self):
        content = config_fixture()
        self.assertEqual(backend.parse_mqtt_settings(content)["client_id"], "device-test")
        corrupt = bytearray(content)
        corrupt[0x9000 + 64 + 64] ^= 1  # First string payload.
        with self.assertRaises(ToolError):
            backend.parse_mqtt_settings(corrupt)
        with self.assertRaisesRegex(ToolError, "官方服务端"):
            backend.parse_mqtt_settings(config_fixture("example.org"))
        with self.assertRaises(ToolError):
            backend.parse_mqtt_settings(content[:100])

    def test_device_restores_after_read_failure_and_cancellation(self):
        cancel = threading.Event()
        with patch.object(backend, "device_ports", return_value=[("COM9", "test")]), \
             patch.object(backend, "read_device_config", side_effect=ToolError("read failed")), \
             patch.object(backend, "restore_device") as restore:
            with self.assertRaises(ToolError):
                with backend.paused_device("COM9", lambda _: None, cancel):
                    self.fail("must not enter after failed read")
            restore.assert_called_once_with("COM9")
        with patch.object(backend, "device_ports", return_value=[("COM9", "test")]), \
             patch.object(backend, "read_device_config", return_value={}), \
             patch.object(backend, "restore_device") as restore:
            with self.assertRaises(backend.Cancelled):
                with backend.paused_device("COM9", lambda _: None, cancel):
                    cancel.set()
                    backend.check_cancel(cancel)
            restore.assert_called_once_with("COM9")

    def test_recovery_failure_is_reported(self):
        with patch.object(backend, "device_ports", return_value=[("COM9", "test")]), \
             patch.object(backend, "read_device_config", return_value={}), \
             patch.object(backend, "restore_device", side_effect=OSError()):
            with self.assertRaisesRegex(ToolError, "恢复失败"):
                with backend.paused_device("COM9", lambda _: None, threading.Event()):
                    pass

    @staticmethod
    @contextmanager
    def no_hardware(*_):
        yield {}

    def test_mismatched_text_never_exports_audio(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(backend, "paused_device", self.no_hardware), \
             patch.object(backend, "request_audio", return_value=[]), \
             patch.object(backend, "delay"), \
             patch.object(backend, "capture_phrase", return_value=(FIXTURE.read_bytes(), "wrong words")) as capture:
            with self.assertRaisesRegex(ToolError, "三次生成"):
                backend.generate("您好", "COM9", directory, "prompt", lambda _: None, threading.Event())
            self.assertEqual(capture.call_count, 3)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_real_conversion_exports_unique_valid_files(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(backend, "paused_device", self.no_hardware), \
             patch.object(backend, "request_audio", return_value=[]), \
             patch.object(backend, "capture_phrase", return_value=(FIXTURE.read_bytes(), "您好。")):
            results = [backend.generate("您好", "COM9", directory, "touch_greeting", lambda _: None, threading.Event()) for _ in range(2)]
            self.assertNotEqual(results[0]["ogg"], results[1]["ogg"])
            for result in results:
                self.assertTrue(Path(result["wav"]).exists())
                self.assertEqual(validate_firmware_ogg(Path(result["ogg"]).read_bytes())["frame_ms"], 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
