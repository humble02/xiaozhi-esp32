#pragma once

#include "driver/touch_sens.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

class SparkBotTouch {
public:
    ~SparkBotTouch();
    esp_err_t Start();

private:
    touch_sensor_handle_t sensor_ = nullptr;
    touch_channel_handle_t channel_ = nullptr;
    TaskHandle_t task_ = nullptr;
    bool enabled_ = false;
    bool scanning_ = false;
    uint32_t threshold_ = 0;

    esp_err_t Initialize();
    void Poll();
    void Cleanup();
};
