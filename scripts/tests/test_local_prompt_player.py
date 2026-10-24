"""Stream the actual embedded Opus prompts through the real bounded prompt player."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class LocalPromptPlayerTests(unittest.TestCase):
    def test_bounded_streaming_drain_cancellation_and_invalid_assets(self):
        compiler = shlex.split(os.environ.get("CXX", "c++"))
        if not compiler or not shutil.which(compiler[0]):
            self.skipTest("requires a host C++20 compiler")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name in ("local_prompt_player.cc", "local_prompt_player.h"):
                shutil.copyfile(ROOT / "main/audio" / name, path / name)
            for name in ("ogg_demuxer.cc", "ogg_demuxer.h"):
                shutil.copyfile(ROOT / "main/audio/demuxer" / name, path / name)
            for name in ("touch_greeting.ogg", "touch_farewell.ogg"):
                shutil.copyfile(ROOT / "main/assets/common" / name, path / name)
            (path / "esp_log.h").write_text(
                "#define ESP_LOGE(...) ((void)0)\n"
                "#define ESP_LOGW(...) ((void)0)\n"
                "#define ESP_LOGD(...) ((void)0)\n", encoding="utf-8",
            )
            (path / "audio_service.h").write_text(r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <memory>
#include <vector>
struct AudioStreamPacket {
    int sample_rate = 0, frame_duration = 0;
    std::vector<uint8_t> payload;
};
struct AudioService {
    std::vector<std::unique_ptr<AudioStreamPacket>> queue;
    bool output_in_flight = false, reject = false;
    int submitted = 0;
    bool PushPacketToDecodeQueue(std::unique_ptr<AudioStreamPacket> packet, bool wait) {
        assert(!wait); // Blocking here would prevent a touch from ending the greeting.
        assert(packet->sample_rate == 16000 && packet->frame_duration == 60);
        if (reject || queue.size() == 2) return false;
        queue.push_back(std::move(packet)); ++submitted; return true;
    }
    bool IsPlaybackIdle() { return queue.empty() && !output_in_flight; }
};
''', encoding="utf-8")
            (path / "test.cc").write_text(r'''
#include "local_prompt_player.h"
#include "audio_service.h"
#include <fstream>
#include <iterator>
#include <string>
std::string read(const char* file) {
    std::ifstream input(file, std::ios::binary);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}
int main() {
    for (const char* file : {"touch_greeting.ogg", "touch_farewell.ogg"}) {
        auto sound = read(file);
        AudioService audio;
        LocalPromptPlayer player(audio);
        assert(player.Start(sound));
        assert(!player.Start(sound)); // another prompt cannot replace this one implicitly
        int batches = 0;
        while (player.IsBusy()) {
            assert(++batches < 100 && audio.queue.size() <= 2);
            assert(!player.OnPlaybackDrained()); // queued audio has not played
            audio.queue.clear();
            audio.output_in_flight = true;
            assert(!player.OnPlaybackDrained()); // the DMA/output tail still owns the last frame
            audio.output_in_flight = false;
            player.OnPlaybackDrained();
        }
        assert(batches > 10 && audio.submitted > 20);
        assert(!player.OnPlaybackDrained()); // completion is reported once
        assert(!player.Start({}));
        assert(!player.Start(std::string_view(sound).substr(0, sound.size() / 2)));
        assert(player.Start(sound));
        player.Stop(); audio.queue.clear();
        assert(!player.OnPlaybackDrained() && !player.IsBusy());
        audio.reject = true;
        assert(!player.Start(sound) && !player.IsBusy());
    }
}
''', encoding="utf-8")
            binary = path / ("prompt-test.exe" if os.name == "nt" else "prompt-test")
            result = subprocess.run(
                compiler + ["-std=c++20", "-I", str(path), str(path / "test.cc"),
                            str(path / "local_prompt_player.cc"), str(path / "ogg_demuxer.cc"),
                            "-o", str(binary)],
                capture_output=True, text=True, errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], cwd=path, capture_output=True,
                                    text=True, errors="replace")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
