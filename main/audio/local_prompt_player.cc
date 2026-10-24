#include "local_prompt_player.h"

#include "audio_service.h"
#include "ogg_demuxer.h"

#include <memory>

bool LocalPromptPlayer::Start(std::string_view ogg) {
    if (active_ || ogg.empty()) {
        return false;
    }
    Stop();
    // Prompts are short, trusted embedded files. Bound both duration and memory.
    constexpr size_t kMaxFrames = 160;
    constexpr size_t kMaxBytes = 64 * 1024;
    constexpr int kMaxDurationMs = 8000;
    size_t bytes = 0;
    int duration_ms = 0;
    bool invalid = false;
    auto demuxer = std::make_unique<OggDemuxer>();
    demuxer->OnPacket([&](const uint8_t* data, int rate, int duration, size_t size) {
        bytes += size;
        duration_ms += duration;
        if (invalid || frames_.size() >= kMaxFrames || bytes > kMaxBytes ||
            duration_ms > kMaxDurationMs) {
            invalid = true;
            return;
        }
        frames_.push_back({rate, duration, std::vector<uint8_t>(data, data + size)});
    });
    demuxer->Process(reinterpret_cast<const uint8_t*>(ogg.data()), ogg.size());
    if (invalid || !demuxer->Finish() || frames_.empty()) {
        Stop();
        return false;
    }
    active_ = true;
    Pump();
    if (next_frame_ == 0) {
        Stop();
        return false;
    }
    return true;
}

void LocalPromptPlayer::Stop() {
    active_ = false;
    next_frame_ = 0;
    frames_.clear();
}

void LocalPromptPlayer::Pump() {
    while (next_frame_ < frames_.size()) {
        const auto& frame = frames_[next_frame_];
        auto packet = std::make_unique<AudioStreamPacket>();
        packet->sample_rate = frame.sample_rate;
        packet->frame_duration = frame.duration_ms;
        packet->payload = frame.data;
        if (!audio_.PushPacketToDecodeQueue(std::move(packet), false)) {
            return;
        }
        ++next_frame_;
    }
}

bool LocalPromptPlayer::OnPlaybackDrained() {
    if (!active_ || !audio_.IsPlaybackIdle()) {
        return false;
    }
    if (next_frame_ < frames_.size()) {
        Pump();
        return false;
    }
    Stop();
    return true;
}
