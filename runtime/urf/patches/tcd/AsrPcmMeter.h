#pragma once
// ASR PCM-only telemetry accumulator for TCD's 160-sample USRP boundaries.
// No PTT, connection state, or encoded voice frames are used as audio evidence.
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>
#include <sys/stat.h>

class CAsrPcmMeter {
public:
    struct Levels { int peak = 0; double rms = 0; double epoch = 0; };
    struct Snapshot { Levels inbound; Levels outbound; };

    void Sample(bool inbound, const int16_t *pcm, size_t count, double epoch) {
        if (!pcm || count != 160 || !std::isfinite(epoch) || epoch <= 0) return;
        int peak = 0;
        double power = 0;
        for (size_t i = 0; i < count; ++i) {
            const int amplitude = pcm[i] < 0 ? -int(pcm[i]) : int(pcm[i]);
            if (amplitude > peak) peak = amplitude;
            power += double(amplitude) * double(amplitude);
        }
        const Levels value{peak, std::sqrt(power / double(count)), epoch};
        std::lock_guard<std::mutex> lock(mutex_);
        (inbound ? inbound_ : outbound_) = value;
    }

    // Called only from the dedicated publisher thread. Audio callbacks never
    // touch the filesystem or wait for slow storage.
    void Publish(double epoch) {
        const char *path = std::getenv("ASR_TCD_AUDIO_METER_PATH");
        if (!path || path[0] != '/' || std::strlen(path) > 512) return;
        // Reject directory traversal and control characters even if a service
        // administrator accidentally supplies a malformed override.
        for (const char *part = path; *part; ++part) {
            if (static_cast<unsigned char>(*part) < 32 || *part == '\x7f') return;
        }
        const size_t path_length = std::strlen(path);
        if (std::strstr(path, "/../") || std::strstr(path, "/./") ||
            (path_length >= 3 && std::strcmp(path + path_length - 3, "/..") == 0) ||
            (path_length >= 2 && std::strcmp(path + path_length - 2, "/.") == 0)) return;
        if (!std::isfinite(epoch) || epoch <= 0) return;
        if (epoch >= last_write_ && epoch - last_write_ < 0.25) return;
        const Snapshot levels = Read(epoch);
        // The audio callback must not fail when telemetry is unavailable.
        // Atomic rename means readers never observe partial PCM samples.
        char tmp[600];
        if (std::snprintf(tmp, sizeof(tmp), "%s.XXXXXX", path) >= int(sizeof(tmp))) return;
        int fd = mkstemp(tmp);
        if (fd < 0) return;
        const auto &rx = levels.inbound;
        const auto &tx = levels.outbound;
        char json[512];
        const int length = std::snprintf(json, sizeof(json),
            "{\"inboundAudioPeak\":%d,\"inboundAudioRms\":%.3f,\"inboundAudioEpoch\":%.6f,"
            "\"outboundAudioPeak\":%d,\"outboundAudioRms\":%.3f,\"outboundAudioEpoch\":%.6f}\n",
            rx.peak, rx.rms, rx.epoch, tx.peak, tx.rms, tx.epoch);
        bool ok = length > 0 && length < int(sizeof(json)) && fchmod(fd, 0644) == 0;
        if (ok) {
            size_t written = 0;
            while (written < size_t(length)) {
                const ssize_t n = write(fd, json + written, size_t(length) - written);
                if (n <= 0) { ok = false; break; }
                written += size_t(n);
            }
        }
        if (close(fd) != 0) ok = false;
        if (ok && rename(tmp, path) == 0) last_write_ = epoch;
        else unlink(tmp);
    }

    Snapshot Read(double now) const {
        std::lock_guard<std::mutex> lock(mutex_);
        Snapshot result{inbound_, outbound_};
        const auto fresh = [now](Levels &value) {
            if (!std::isfinite(now) || now < value.epoch || now - value.epoch >= 0.75)
                value = Levels{};
        };
        fresh(result.inbound);
        fresh(result.outbound);
        return result;
    }

private:
    mutable std::mutex mutex_;
    Levels inbound_, outbound_;
    double last_write_ = 0;
};
