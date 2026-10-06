#pragma once
#include <chrono>
#include <fstream>
#include <mutex>
#include <string>
#include <cstdio>

static inline void AsrPttState(bool active, const char *mode)
{
    static std::mutex mutex;
    std::lock_guard<std::mutex> lock(mutex);
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    const char *tmp = "/data/asr-live-state.json.tmp";
    const char *dst = "/data/asr-live-state.json";
    std::ofstream out(tmp, std::ios::trunc);
    if (!out) return;
    out << "{\"active\":" << (active ? "true" : "false")
        << ",\"mode\":\"" << (mode ? mode : "")
        << "\",\"updated_epoch_ms\":" << ms << "}" << std::endl;
    out.close();
    std::rename(tmp, dst);
}
