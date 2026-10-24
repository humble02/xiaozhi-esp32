#include "sparkbot_touch.h"

#include "application.h"
#include "touch_press_filter.h"

#include <esp_check.h>
#include <esp_log.h>
#include <esp_timer.h>

#include <algorithm>

static const char* TAG = "SparkBotTouch";

SparkBotTouch::~SparkBotTouch() { Cleanup(); }

void SparkBotTouch::Cleanup() {
    if (task_) {
        vTaskDelete(task_);
        task_ = nullptr;
    }
    if (scanning_) {
        touch_sensor_stop_continuous_scanning(sensor_);
        scanning_ = false;
    }
    if (enabled_) {
        touch_sensor_disable(sensor_);
        enabled_ = false;
    }
    if (channel_) {
        touch_sensor_del_channel(channel_);
        channel_ = nullptr;
    }
    if (sensor_) {
        touch_sensor_del_controller(sensor_);
        sensor_ = nullptr;
    }
}

esp_err_t SparkBotTouch::Start() {
    if (sensor_) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t error = Initialize();
    if (error != ESP_OK) {
        Cleanup();
        return error;
    }
    if (xTaskCreate([](void* arg) { static_cast<SparkBotTouch*>(arg)->Poll(); }, "sparkbot_touch",
                    3072, this, 3, &task_) != pdPASS) {
        Cleanup();
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t SparkBotTouch::Initialize() {
    // IDF 6 replaces the legacy touch_pad/touch_element APIs used by the old BSP.
    touch_sensor_sample_config_t sample =
        TOUCH_SENSOR_V2_DEFAULT_SAMPLE_CONFIG(500, TOUCH_VOLT_LIM_L_0V5, TOUCH_VOLT_LIM_H_2V2);
    touch_sensor_config_t sensor_config = TOUCH_SENSOR_DEFAULT_BASIC_CONFIG(1, &sample);
    ESP_RETURN_ON_ERROR(touch_sensor_new_controller(&sensor_config, &sensor_), TAG,
                        "Cannot create touch controller");
    touch_channel_config_t channel_config = {
        .active_thresh = {2000},
        .charge_speed = TOUCH_CHARGE_SPEED_7,
        .init_charge_volt = TOUCH_INIT_CHARGE_VOLT_DEFAULT,
    };
    ESP_RETURN_ON_ERROR(touch_sensor_new_channel(sensor_, CONFIG_SPARKBOT_TOUCH_CHANNEL,
                                                 &channel_config, &channel_),
                        TAG, "Cannot create touch channel");
    touch_sensor_filter_config_t filter = TOUCH_SENSOR_DEFAULT_FILTER_CONFIG();
    ESP_RETURN_ON_ERROR(touch_sensor_config_filter(sensor_, &filter), TAG,
                        "Cannot configure touch filter");
    ESP_RETURN_ON_ERROR(touch_sensor_enable(sensor_), TAG, "Cannot enable touch controller");
    enabled_ = true;
    for (int i = 0; i < 3; ++i) {
        ESP_RETURN_ON_ERROR(touch_sensor_trigger_oneshot_scanning(sensor_, 2000), TAG,
                            "Touch calibration failed");
    }
    ESP_RETURN_ON_ERROR(touch_sensor_disable(sensor_), TAG, "Cannot finish touch calibration");
    enabled_ = false;
    uint32_t benchmark = 0;
    ESP_RETURN_ON_ERROR(
        touch_channel_read_data(channel_, TOUCH_CHAN_DATA_TYPE_BENCHMARK, &benchmark), TAG,
        "Cannot read touch baseline");
    if (benchmark == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    threshold_ = std::max<uint32_t>(
        1, static_cast<uint64_t>(benchmark) * CONFIG_SPARKBOT_TOUCH_THRESHOLD_PERMILLE / 1000);
    channel_config.active_thresh[0] = threshold_;
    ESP_RETURN_ON_ERROR(touch_sensor_reconfig_channel(channel_, &channel_config), TAG,
                        "Cannot set touch threshold");
    ESP_RETURN_ON_ERROR(touch_sensor_enable(sensor_), TAG, "Cannot enable calibrated touch");
    enabled_ = true;
    ESP_RETURN_ON_ERROR(touch_sensor_start_continuous_scanning(sensor_), TAG,
                        "Cannot start touch scanning");
    scanning_ = true;
    ESP_LOGI(TAG, "Top touch: channel=%d, baseline=%lu, threshold=%lu",
             CONFIG_SPARKBOT_TOUCH_CHANNEL, static_cast<unsigned long>(benchmark),
             static_cast<unsigned long>(threshold_));
    return ESP_OK;
}

void SparkBotTouch::Poll() {
    TouchPressFilter filter;
    while (true) {
        uint32_t smooth = 0;
        uint32_t benchmark = 0;
        if (touch_channel_read_data(channel_, TOUCH_CHAN_DATA_TYPE_SMOOTH, &smooth) == ESP_OK &&
            touch_channel_read_data(channel_, TOUCH_CHAN_DATA_TYPE_BENCHMARK, &benchmark) ==
                ESP_OK) {
            // Hysteresis plus three stable samples suppress chatter and long-hold repeats.
            const uint32_t threshold = filter.IsPressed() ? threshold_ / 2 : threshold_;
            const bool active = smooth > benchmark && smooth - benchmark > threshold;
            if (filter.Update(active, esp_timer_get_time())) {
                ESP_LOGI(TAG, "Top touch pressed");
                Application::GetInstance().ToggleConversation();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
