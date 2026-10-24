#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>
#include <vector>

class AudioService;

// Main-task-only player for short embedded prompts. Never waits for queue space.
class LocalPromptPlayer {
public:
    explicit LocalPromptPlayer(AudioService& audio) : audio_(audio) {}
    bool Start(std::string_view ogg);
    void Stop();
    bool OnPlaybackDrained();  // True only after every queued frame has played.
    bool IsBusy() const { return active_; }

private:
    struct Frame {
        int sample_rate;
        int duration_ms;
        std::vector<uint8_t> data;
    };
    AudioService& audio_;
    std::vector<Frame> frames_;
    size_t next_frame_ = 0;
    bool active_ = false;

    void Pump();
};
