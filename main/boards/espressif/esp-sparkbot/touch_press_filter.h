#pragma once

#include <cstdint>

// Called once per 20 ms sample. Require a stable release before another press.
class TouchPressFilter {
public:
    bool Update(bool active, int64_t now_us) {
        if (active != candidate_) {
            candidate_ = active;
            samples_ = 1;
        } else if (samples_ < 3) {
            ++samples_;
        }
        if (samples_ < 3 || stable_ == candidate_) {
            return false;
        }
        stable_ = candidate_;
        if (!stable_ || now_us - last_press_us_ < 400000) {
            return false;
        }
        last_press_us_ = now_us;
        return true;
    }

    bool IsPressed() const { return stable_; }

private:
    bool candidate_ = false;
    bool stable_ = false;
    int samples_ = 0;
    int64_t last_press_us_ = -400000;
};
