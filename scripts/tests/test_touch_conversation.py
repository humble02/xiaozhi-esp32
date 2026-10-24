"""Execute touch debounce and real application handlers with host-side transports."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def definition(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class TouchConversationTests(unittest.TestCase):
    def compile_and_run(self, source):
        compiler = shlex.split(os.environ.get("CXX", "c++"))
        if not compiler or not shutil.which(compiler[0]):
            self.skipTest("requires a host C++20 compiler")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            cpp = path / "touch.cc"
            cpp.write_text(source, encoding="utf-8")
            binary = path / ("touch.exe" if os.name == "nt" else "touch")
            result = subprocess.run(
                compiler + ["-std=c++20", "-Wall", "-Wextra", str(cpp), "-o", str(binary)],
                capture_output=True, text=True, errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, errors="replace")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_noise_long_hold_release_and_cooldown(self):
        header = (ROOT / "main/boards/espressif/esp-sparkbot/touch_press_filter.h").read_text(
            encoding="utf-8"
        ).replace("#pragma once", "")
        self.compile_and_run(r'''
#include <cassert>
#include "HEADER"
int main() {
    TouchPressFilter filter;
    assert(!filter.Update(true, 0));
    assert(!filter.Update(false, 20000));
    assert(!filter.Update(true, 40000));
    assert(!filter.Update(true, 60000));
    assert(filter.Update(true, 80000));
    for (int t = 100000; t < 2000000; t += 20000) assert(!filter.Update(true, t));
    // A short release glitch cannot rearm the input.
    assert(!filter.Update(false, 2000000));
    assert(!filter.Update(true, 2020000));
    assert(!filter.Update(true, 2040000));
    assert(!filter.Update(true, 2060000));
    for (int t = 2080000; t <= 2120000; t += 20000) assert(!filter.Update(false, t));
    assert(!filter.Update(true, 2140000));
    assert(!filter.Update(true, 2160000));
    assert(filter.Update(true, 2180000));
    for (int t = 2200000; t <= 2240000; t += 20000) assert(!filter.Update(false, t));
    assert(!filter.Update(true, 2260000));
    assert(!filter.Update(true, 2280000));
    assert(!filter.Update(true, 2300000));  // inside the 400ms cooldown
    assert(!filter.Update(true, 2800000));  // holding never becomes a delayed press
    for (int t = 2820000; t <= 2860000; t += 20000) assert(!filter.Update(false, t));
    assert(!filter.Update(true, 2880000));
    assert(!filter.Update(true, 2900000));
    assert(filter.Update(true, 2920000));
}
'''.replace('#include "HEADER"', header))

    def test_start_end_cancel_and_late_tts_for_both_transport_shapes(self):
        source = (ROOT / "main/application.cc").read_text(encoding="utf-8")
        methods = "\n".join(definition(source, signature) for signature in (
            "void Application::ToggleConversation()",
            "void Application::HandleToggleConversationEvent()",
            "void Application::StartTouchPrompt(TouchPrompt prompt)",
            "void Application::HandleTouchPromptDrained()",
            "void Application::EndConversation()",
            "void Application::EndConversationWithFarewell()",
            "void Application::StopNotification()",
            "void Application::HandleToggleChatEvent()",
            "void Application::ContinueOpenAudioChannel(ListeningMode mode)",
            "void Application::ContinueWakeWordInvoke(const std::string& wake_word)",
        ))
        closed = source.split("protocol_->OnAudioChannelClosed(", 1)[1]
        scheduled = definition(closed, "Schedule([this]()")
        methods += "\nvoid Application::HandleClosed() " + scheduled[scheduled.index("{"):]
        tts = source.split('} else if (strcmp(type->valuestring, "tts") == 0)', 1)[1]
        for state, method in (("start", "TtsStart"), ("stop", "TtsStop")):
            branch = tts.split(f'strcmp(state->valuestring, "{state}") == 0', 1)[1]
            scheduled = definition(branch, "Schedule([this]()")
            body = scheduled[scheduled.index("{"):]
            methods += f"\nvoid Application::{method}() {body}\n"
        platform = r'''
#include <cassert>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define CONFIG_SEND_WAKE_WORD_DATA 0
#define CONFIG_SPARKBOT_TOUCH_CONVERSATION 0
namespace Lang::Sounds {
const std::string_view OGG_TOUCH_GREETING = "greeting";
const std::string_view OGG_TOUCH_FAREWELL = "farewell";
}
constexpr unsigned MAIN_EVENT_TOGGLE_CONVERSATION = 1 << 15;
constexpr unsigned MAIN_EVENT_WAKE_WORD_DETECTED = 1 << 2;
unsigned xEventGroupClearBits(unsigned* events, unsigned mask) {
    unsigned old = *events; *events &= ~mask; return old;
}
void xEventGroupSetBits(unsigned* events, unsigned mask) { *events |= mask; }
enum DeviceState {
    kDeviceStateIdle, kDeviceStateConnecting, kDeviceStateListening, kDeviceStateSpeaking,
    kDeviceStateNotifying, kDeviceStateStarting, kDeviceStateActivating,
    kDeviceStateWifiConfiguring, kDeviceStateAudioTesting, kDeviceStateUpgrading
};
enum ListeningMode { kListeningModeAutoStop, kListeningModeManualStop };
enum AbortReason { kAbortReasonNone };
enum class PowerSaveLevel { LOW_POWER, PERFORMANCE };
struct Display {
    std::string text;
    void SetChatMessage(const char*, const char* message) { text = message; }
};
struct Board {
    Display display;
    PowerSaveLevel level = PowerSaveLevel::LOW_POWER;
    static Board& GetInstance() { static Board b; return b; }
    void SetPowerSaveLevel(PowerSaveLevel next) { level = next; }
    Display* GetDisplay() { return &display; }
};
struct PromptPlayer {
    bool active = false, drained = false;
    int starts = 0;
    std::string_view sound;
    bool Start(std::string_view value) { active = true; drained = false; sound = value; ++starts; return true; }
    void Stop() { active = false; drained = false; }
    bool OnPlaybackDrained() { if (!active || !drained) return false; active = false; return true; }
};
struct NotifyPlayer {
    void Stop() {}
    bool IsBusy() { return false; }
};
struct Audio {
    bool recording = true, testing = false, wake = true;
    int pending = 3, decodes = 4, resets = 0;
    void EnableVoiceProcessing(bool value) { recording = value; }
    void EnableAudioTesting(bool value) { testing = value; }
    void EnableWakeWordDetection(bool value) { wake = value; }
    void ResetDecoder() { decodes = 0; ++resets; }
    bool PopPacketFromSendQueue() { if (!pending) return false; --pending; return true; }
};
// Model both an immediate close callback (MQTT) and a deferred callback (WebSocket).
struct Protocol {
    bool opened = false, succeeds = true, deferred_close = false;
    int opens = 0, closes = 0, stops = 0, aborts = 0;
    std::function<void()> during_open, on_close, pending_close;
    bool IsAudioChannelOpened() { return opened; }
    bool OpenAudioChannel() {
        ++opens; if (during_open) during_open(); opened = succeeds; return opened;
    }
    void SendStopListening() { ++stops; }
    void CloseAudioChannel() {
        ++closes; opened = false;
        if (deferred_close) pending_close = on_close; else if (on_close) on_close();
    }
};
struct Application {
    enum class TouchPrompt { None, Greeting, Farewell };
    TouchPrompt touch_prompt_ = TouchPrompt::None;
    PromptPlayer touch_prompt_player_;
    NotifyPlayer notify_player_;
    unsigned events = 0;
    unsigned* event_group_ = &events;
    DeviceState state = kDeviceStateIdle;
    bool conversation_stopped_ = false, pending_listening_start_ = false;
    bool play_popup_on_listening_ = false, aborted_ = false;
    ListeningMode listening_mode_ = kListeningModeAutoStop;
    Audio audio_service_;
    std::unique_ptr<Protocol> protocol_ = std::make_unique<Protocol>();
    std::vector<std::function<void()>> scheduled;
    DeviceState GetDeviceState() { return state; }
    bool SetDeviceState(DeviceState next) { state = next; return true; }
    ListeningMode GetDefaultListeningMode() { return kListeningModeAutoStop; }
    void SetListeningMode(ListeningMode mode) { listening_mode_ = mode; state = kDeviceStateListening; }
    void AbortSpeaking(AbortReason) { aborted_ = true; ++protocol_->aborts; }
    void StopNotification();
    void Schedule(std::function<void()> callback) { scheduled.push_back(std::move(callback)); }
    void RunScheduled() {
        auto tasks = std::move(scheduled); scheduled.clear(); for (auto& task : tasks) task();
    }
    void ToggleConversation();
    void HandleToggleConversationEvent();
    void EndConversation();
    void EndConversationWithFarewell();
    void StartTouchPrompt(TouchPrompt prompt);
    void HandleTouchPromptDrained();
    void HandleClosed();
    void HandleToggleChatEvent();
    void ContinueOpenAudioChannel(ListeningMode mode);
    void ContinueWakeWordInvoke(const std::string& wake_word);
    void TtsStart();
    void TtsStop();
};
'''
        driver = r'''
int main() {
    for (bool deferred : {false, true}) {
        Application a;
        a.protocol_->deferred_close = deferred;
        a.protocol_->on_close = [&] { a.Schedule([&] { a.SetDeviceState(kDeviceStateIdle); }); };
        a.HandleToggleConversationEvent();
        assert(a.state == kDeviceStateConnecting);
        a.RunScheduled();
        assert(a.state == kDeviceStateListening && a.play_popup_on_listening_);
        a.TtsStart();
        assert(a.state == kDeviceStateSpeaking);
        a.pending_listening_start_ = true;
        a.events |= MAIN_EVENT_WAKE_WORD_DETECTED;
        a.HandleToggleConversationEvent();
        assert(a.state == kDeviceStateIdle && a.conversation_stopped_);
        assert(!a.audio_service_.recording && a.audio_service_.decodes == 0);
        assert(a.audio_service_.pending == 0 && a.protocol_->closes == 1);
        assert(a.protocol_->stops == 1 && a.protocol_->aborts == 1);
        assert(!a.pending_listening_start_ && !a.play_popup_on_listening_);
        assert(!(a.events & MAIN_EVENT_WAKE_WORD_DETECTED));
        a.TtsStart(); a.TtsStop();
        assert(a.state == kDeviceStateIdle);
        if (a.protocol_->pending_close) a.protocol_->pending_close();
        a.RunScheduled();
        a.HandleToggleConversationEvent(); a.RunScheduled();
        assert(a.state == kDeviceStateListening && !a.conversation_stopped_);
        a.HandleToggleConversationEvent(); // Stop during recording as well as playback.
        assert(a.state == kDeviceStateIdle && !a.protocol_->opened);
    }
    for (bool wake : {false, true}) {
        for (bool success : {false, true}) {
            Application a;
            a.state = kDeviceStateConnecting;
            a.protocol_->succeeds = success;
            a.protocol_->during_open = [&] { a.ToggleConversation(); };
            if (wake) a.ContinueWakeWordInvoke("hello");
            else a.ContinueOpenAudioChannel(kListeningModeAutoStop);
            assert(a.state == kDeviceStateIdle && a.conversation_stopped_);
            assert(!a.protocol_->opened && a.protocol_->closes == 1);
            assert(!(a.events & MAIN_EVENT_TOGGLE_CONVERSATION));
        }
    }
    Application early;
    early.HandleToggleConversationEvent();
    early.ToggleConversation();  // Second touch before the deferred open even starts.
    early.RunScheduled();
    assert(early.state == kDeviceStateIdle && early.protocol_->opens == 0);
    for (auto state : {kDeviceStateStarting, kDeviceStateActivating,
                       kDeviceStateWifiConfiguring, kDeviceStateUpgrading}) {
        Application a; a.state = state;
        a.HandleToggleConversationEvent();
        assert(a.state == state && a.protocol_->opens == 0 && !a.audio_service_.testing);
    }
    Application offline;
    offline.protocol_.reset();
    offline.HandleToggleConversationEvent();
    assert(offline.state == kDeviceStateIdle);
}
'''
        self.compile_and_run(platform + methods + driver)
        prompt_driver = r'''
int main() {
    for (bool deferred : {false, true}) {
        Application a;
        a.protocol_->deferred_close = deferred;
        a.protocol_->on_close = [&] { a.Schedule([&] { a.HandleClosed(); }); };
        a.HandleToggleConversationEvent();
        assert(a.touch_prompt_ == Application::TouchPrompt::Greeting);
        assert(a.state == kDeviceStateNotifying && a.protocol_->opens == 0);
        assert(!a.audio_service_.recording && !a.audio_service_.wake);
        assert(a.touch_prompt_player_.sound == "greeting");
        a.TtsStart(); assert(a.state == kDeviceStateNotifying); // stale remote speech ignored
        a.HandleTouchPromptDrained(); assert(a.protocol_->opens == 0);
        a.touch_prompt_player_.drained = true;
        a.HandleTouchPromptDrained();
        assert(a.state == kDeviceStateConnecting && a.protocol_->opens == 0);
        a.RunScheduled(); assert(a.state == kDeviceStateListening);
        assert(!a.play_popup_on_listening_); // no second beep after the spoken greeting
        a.TtsStart(); assert(a.state == kDeviceStateSpeaking);
        a.HandleToggleConversationEvent();
        assert(a.state == kDeviceStateNotifying && a.conversation_stopped_);
        assert(a.touch_prompt_ == Application::TouchPrompt::Farewell);
        assert(!a.protocol_->opened && a.protocol_->stops == 1 && a.protocol_->aborts == 1);
        assert(!a.audio_service_.recording && !a.audio_service_.wake);
        assert(a.touch_prompt_player_.sound == "farewell");
        if (a.protocol_->pending_close) a.protocol_->pending_close();
        a.RunScheduled(); assert(a.state == kDeviceStateNotifying); // late close cannot cut goodbye
        a.HandleToggleConversationEvent(); assert(a.touch_prompt_player_.starts == 2);
        a.TtsStart(); a.TtsStop(); assert(a.state == kDeviceStateNotifying);
        a.touch_prompt_player_.drained = true;
        a.HandleTouchPromptDrained();
        assert(a.state == kDeviceStateIdle && a.protocol_->opens == 1);
        assert(a.touch_prompt_ == Application::TouchPrompt::None);
    }
    Application cancelled;
    cancelled.HandleToggleConversationEvent();
    cancelled.HandleToggleConversationEvent(); // second tap during greeting
    assert(cancelled.touch_prompt_ == Application::TouchPrompt::Farewell);
    cancelled.touch_prompt_player_.drained = true;
    cancelled.HandleTouchPromptDrained(); cancelled.RunScheduled();
    assert(cancelled.state == kDeviceStateIdle && cancelled.protocol_->opens == 0);
    Application disconnected;
    disconnected.HandleToggleConversationEvent();
    disconnected.StopNotification(); // network error cancels a local prompt too
    disconnected.touch_prompt_player_.drained = true;
    disconnected.HandleTouchPromptDrained(); disconnected.RunScheduled();
    assert(disconnected.state == kDeviceStateIdle && disconnected.protocol_->opens == 0);
}
'''
        self.compile_and_run(platform.replace(
            "#define CONFIG_SPARKBOT_TOUCH_CONVERSATION 0",
            "#define CONFIG_SPARKBOT_TOUCH_CONVERSATION 1",
        ) + methods + prompt_driver)


if __name__ == "__main__":
    unittest.main()
