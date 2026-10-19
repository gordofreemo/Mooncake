// Copyright 2025 Alibaba Cloud and its affiliates
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <fstream>
#include <future>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <latch>
#include <mutex>
#include <random>
#include <sstream>
#include <thread>
#include <vector>

#include "gflags/gflags.h"
#include "glog/logging.h"

#include "master_client.h"

// Size units for better readability
static constexpr size_t KiB = 1024;
static constexpr size_t MiB = 1024 * KiB;
static constexpr size_t GiB = 1024 * MiB;
static constexpr uintptr_t kSegmentBase = 0x100000000ULL;

DEFINE_string(master_server, "127.0.0.1:50051", "Master server address");
DEFINE_uint64(num_segments, 128, "Number of segments to mount");
DEFINE_uint64(segment_size, 64 * GiB, "Size of each segment");
DEFINE_uint64(num_clients, 4, "Number of clients to perform operations");
DEFINE_uint64(num_threads, 1,
              "Number of threads in each client to perform operations");
DEFINE_string(operation, "BatchPut", "Operation to perform");
DEFINE_uint64(num_keys, 10 * 1000,
              "Number of keys to prefill for Get operations on each thread");
DEFINE_uint64(batch_size, 128, "Batch size for batch operations");
DEFINE_uint64(value_size, 4096, "Size of object values");
DEFINE_uint64(duration, 60, "Test duration in seconds");
DEFINE_double(
    prefill_ratio, 0.0,
    "Ratio of segment capacity to prefill before test (0.0-1.0). "
    "E.g., 0.95 means fill segments to 95% before starting benchmark");

// masterbench additions. Defaults keep the upstream behavior.
DEFINE_uint64(prefill_objects, 0,
              "Number of objects to prefill (overrides prefill_ratio if > 0)");
DEFINE_uint64(prefill_threads, 1, "Number of threads used for prefilling");
DEFINE_bool(get_from_prefill, false,
            "Get/BatchGet draw keys uniformly at random from the prefilled "
            "keys instead of per-thread keys (no per-thread prefill)");
DEFINE_bool(keep_cpu_affinity, false,
            "Do not reset worker threads' CPU affinity, so taskset applies");
DEFINE_uint64(warmup, 0, "Seconds to run before the measurement window");
DEFINE_string(clients_list, "",
              "Comma-separated client counts to sweep in one process, e.g. "
              "1,2,4 (empty: run once with --num_clients)");
DEFINE_uint64(repeats, 1, "Repeats per client count");
DEFINE_uint64(repeat_offset, 0, "Added to the repeat index in RESULT lines");
DEFINE_uint64(pause, 0, "Seconds to pause between measurement points");
DEFINE_int64(master_pid, 0,
             "PID of mooncake_master in the same PID namespace; if set, its "
             "CPU time and RSS are reported per point");
DEFINE_string(result_tag, "", "Free-form tag echoed in each RESULT line");

static inline void unset_cpu_affinity() {
    if (FLAGS_keep_cpu_affinity) {
        return;
    }
    // Ensure that the worker threads are not bound to any CPU cores.
    cpu_set_t cpuset;
    memset(&cpuset, -1, sizeof(cpuset));
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);
}

// CPU seconds (user + system) consumed so far by a process, from /proc.
static double ProcCpuSeconds(int64_t pid) {
    std::ifstream stat_file("/proc/" + std::to_string(pid) + "/stat");
    std::string content((std::istreambuf_iterator<char>(stat_file)),
                        std::istreambuf_iterator<char>());
    auto close_paren = content.rfind(')');
    if (close_paren == std::string::npos) {
        return -1.0;
    }
    // Fields after the command name start at field 3; utime and stime are
    // fields 14 and 15.
    std::istringstream rest(content.substr(close_paren + 2));
    std::string field;
    uint64_t utime = 0, stime = 0;
    for (int i = 3; i <= 15 && rest >> field; i++) {
        if (i == 14) utime = std::stoull(field);
        if (i == 15) stime = std::stoull(field);
    }
    return static_cast<double>(utime + stime) / sysconf(_SC_CLK_TCK);
}

// Resident set size of a process in bytes, from /proc.
static uint64_t ProcRssBytes(int64_t pid) {
    std::ifstream status_file("/proc/" + std::to_string(pid) + "/status");
    std::string line;
    while (std::getline(status_file, line)) {
        if (line.rfind("VmRSS:", 0) == 0) {
            std::istringstream fields(line.substr(6));
            uint64_t kib = 0;
            fields >> kib;
            return kib * 1024;
        }
    }
    return 0;
}

class SegmentClient {
   public:
    SegmentClient(const std::string& name, const std::string& master_server,
                  uintptr_t segment_base, uint64_t segment_size)
        : master_client_(mooncake::generate_uuid()) {
        auto ec = master_client_.Connect(master_server);
        if (ec != mooncake::ErrorCode::OK) {
            throw std::invalid_argument("Cannot connect to master server at " +
                                        master_server + ", ec=" + toString(ec));
        }

        segment_.id = mooncake::generate_uuid();
        segment_.name = name;
        segment_.base = segment_base;
        segment_.size = segment_size;
        segment_.te_endpoint = name;
        auto mount_ec = master_client_.MountSegment(segment_);
        if (!mount_ec.has_value()) {
            throw std::runtime_error("Failed to mount segment " + name +
                                     ", ec=" + toString(mount_ec.error()));
        }
    }

    ~SegmentClient() {
        if (remount_future_.valid()) {
            remount_future_.wait();
        }

        auto unmount_result = master_client_.UnmountSegment(segment_.id);
        if (!unmount_result.has_value()) {
            LOG(ERROR) << "Failed to unmount segment " << segment_.name;
        }
    }

    void Ping() {
        if (remount_future_.valid() &&
            remount_future_.wait_for(std::chrono::seconds(0)) ==
                std::future_status::ready) {
            remount_future_.get();
            remount_future_ = std::future<void>();
        }

        auto ping_result = master_client_.Ping();
        if (!ping_result.has_value()) {
            throw std::runtime_error("Failed to ping master server");
        }

        if (ping_result.value().client_status ==
                mooncake::ClientStatus::NEED_REMOUNT &&
            !remount_future_.valid()) {
            remount_future_ = std::async(std::launch::async, [&]() {
                auto remount_ec = master_client_.ReMountSegment({segment_});
                if (!remount_ec.has_value()) {
                    throw std::runtime_error("Failed to remount segment");
                }
            });
        }
    }

   private:
    mooncake::MasterClient master_client_;
    mooncake::Segment segment_;
    std::future<void> remount_future_;
};

static std::atomic<uint64_t> gCompletedOperations = 0;

// Measurement window state. Calls that complete while gMeasuring is set are
// counted and timed; calls completing outside the window are not.
static std::atomic<bool> gMeasuring = false;
static std::atomic<uint64_t> gCalls = 0;
static std::atomic<uint64_t> gKeysOk = 0;
static std::atomic<uint64_t> gKeysNotFound = 0;
static std::atomic<uint64_t> gKeysErr = 0;
static std::mutex gLatencyMutex;
static std::vector<double> gLatenciesUs;
// Number of "prefill_<i>" keys present in the master (Get key range).
static uint64_t gPrefillCount = 0;

enum class BenchOperation {
    PUT,
    GET,
    BATCH_PUT,
    BATCH_GET,
};

static inline BenchOperation ParseOperation(const std::string& operation_str) {
    if (operation_str == "Put") {
        return BenchOperation::PUT;
    } else if (operation_str == "Get") {
        return BenchOperation::GET;
    } else if (operation_str == "BatchPut") {
        return BenchOperation::BATCH_PUT;
    } else if (operation_str == "BatchGet") {
        return BenchOperation::BATCH_GET;
    } else {
        throw std::invalid_argument("Invalid operation");
    }
}

// Outcome of one call: how many keys succeeded, were absent, or failed.
struct CallOutcome {
    uint64_t ok = 0;
    uint64_t not_found = 0;
    uint64_t err = 0;
};

class BenchClient {
   public:
    BenchClient(const std::string& master_server)
        : master_client_(mooncake::generate_uuid()), running_(false) {
        auto ec = master_client_.Connect(master_server);
        if (ec != mooncake::ErrorCode::OK) {
            throw std::invalid_argument("Cannot connect to master server at " +
                                        master_server + ", ec=" + toString(ec));
        }
    }

    void StartBench(std::shared_ptr<std::latch> barrier,
                    BenchOperation operation, uint64_t num_threads,
                    uint64_t batch_size, uint64_t value_size,
                    uint64_t num_keys) {
        if (running_.load()) {
            return;
        }

        running_.store(true);
        for (size_t i = 0; i < num_threads; i++) {
            threads_.push_back(std::thread(
                std::bind(&BenchClient::BenchFn, this, barrier, operation,
                          batch_size, value_size, num_keys)));
        }
    }

    void StopBench() {
        if (!running_.load()) {
            return;
        }

        running_.store(false);
        for (auto& thread : threads_) {
            if (thread.joinable()) {
                thread.join();
            }
        }

        threads_.clear();
    }

   private:
    CallOutcome Put(const std::string& key,
                    const std::vector<uint64_t>& slice_lengths,
                    const mooncake::ReplicateConfig& config) {
        auto put_start_result =
            master_client_.PutStart(key, slice_lengths, config);
        if (!put_start_result.has_value()) {
            return {0, 0, 1};
        }

        auto put_end_result = master_client_.PutEnd(
            {key, std::nullopt}, mooncake::ReplicaType::MEMORY);
        if (!put_end_result.has_value()) {
            return {0, 0, 1};
        }

        return {1, 0, 0};
    }

    CallOutcome Get(const std::string& key) {
        auto get_result = master_client_.GetReplicaList(key);
        if (get_result.has_value()) {
            return {1, 0, 0};
        }
        if (get_result.error() == mooncake::ErrorCode::OBJECT_NOT_FOUND) {
            return {0, 1, 0};
        }
        return {0, 0, 1};
    }

    CallOutcome BatchPut(const std::vector<std::string>& keys,
                         const std::vector<std::vector<uint64_t>>& slice_lengths,
                         const mooncake::ReplicateConfig& config) {
        std::vector<std::string> started_keys;
        uint64_t success_cnt = 0;

        auto put_start_result =
            master_client_.BatchPutStart(keys, slice_lengths, config);
        for (size_t i = 0; i < keys.size(); i++) {
            if (put_start_result[i].has_value()) {
                started_keys.push_back(keys[i]);
            }
        }

        if (started_keys.empty()) {
            return {0, 0, keys.size()};
        }

        std::vector<mooncake::ObjectMeta> object_metas;
        object_metas.reserve(started_keys.size());
        for (const auto& key : started_keys) {
            object_metas.emplace_back(mooncake::ObjectMeta{key, std::nullopt});
        }
        auto put_end_result = master_client_.BatchPutEnd(object_metas);
        for (auto& result : put_end_result) {
            if (result.has_value()) {
                success_cnt++;
            }
        }

        return {success_cnt, 0, keys.size() - success_cnt};
    }

    CallOutcome BatchGet(const std::vector<std::string>& keys) {
        CallOutcome outcome;
        auto get_results = master_client_.BatchGetReplicaList(keys);
        for (auto& get_result : get_results) {
            if (get_result.has_value()) {
                outcome.ok++;
            } else if (get_result.error() ==
                       mooncake::ErrorCode::OBJECT_NOT_FOUND) {
                outcome.not_found++;
            } else {
                outcome.err++;
            }
        }
        return outcome;
    }

    std::vector<std::string> GeneratePutKeys(uint64_t& key_id,
                                             uint64_t num_keys) {
        const auto self_id = std::this_thread::get_id();
        std::vector<std::string> keys;
        keys.reserve(num_keys);

        for (size_t i = 0; i < num_keys; i++, key_id++) {
            std::stringstream ss;
            ss << self_id << "_" << key_id;
            keys.push_back(ss.str());
        }

        return keys;
    }

    std::vector<std::string> GenerateGetKeys(uint64_t key_id,
                                             uint64_t num_keys) {
        static thread_local std::mt19937_64 generator(std::random_device{}());

        std::vector<std::string> keys;
        keys.reserve(num_keys);

        if (FLAGS_get_from_prefill) {
            if (gPrefillCount == 0) {
                return keys;
            }
            std::uniform_int_distribution<uint64_t> distribution(
                0, gPrefillCount - 1);
            for (size_t i = 0; i < num_keys; i++) {
                keys.push_back("prefill_" +
                               std::to_string(distribution(generator)));
            }
            return keys;
        }

        const auto self_id = std::this_thread::get_id();

        if (key_id == 0) {
            // No keys have been created yet; return empty vector.
            return keys;
        }

        std::uniform_int_distribution<uint64_t> distribution(0, key_id - 1);
        for (size_t i = 0; i < num_keys; i++) {
            std::stringstream ss;
            ss << self_id << "_" << distribution(generator);
            keys.push_back(ss.str());
        }

        return keys;
    }

    void PrefillKeys(uint64_t& key_id, uint64_t batch_size, uint64_t value_size,
                     uint64_t num_keys) {
        const mooncake::ReplicateConfig config;

        uint64_t num_to_prefill = std::min(batch_size, num_keys);
        while (num_to_prefill > 0) {
            auto keys = GeneratePutKeys(key_id, num_to_prefill);
            std::vector<std::vector<uint64_t>> slice_lengths(num_to_prefill,
                                                             {value_size});
            BatchPut(keys, slice_lengths, config);
            num_keys -= num_to_prefill;
            num_to_prefill = std::min(batch_size, num_keys);
        }
    }

    void BenchFn(std::shared_ptr<std::latch> barrier, BenchOperation operation,
                 uint64_t batch_size, uint64_t value_size, uint64_t num_keys) {
        unset_cpu_affinity();

        uint64_t key_id = 0;
        const mooncake::ReplicateConfig config;

        std::vector<std::string> keys;
        std::vector<std::vector<uint64_t>> slice_lengths;
        slice_lengths.reserve(batch_size);
        for (size_t i = 0; i < batch_size; i++) {
            slice_lengths.push_back({value_size});
        }

        if ((operation == BenchOperation::GET ||
             operation == BenchOperation::BATCH_GET) &&
            !FLAGS_get_from_prefill) {
            PrefillKeys(key_id, batch_size, value_size, num_keys);
        }

        std::vector<double> latencies_us;
        latencies_us.reserve(1 << 16);

        barrier->arrive_and_wait();

        while (running_.load()) {
            CallOutcome outcome;
            // Key generation happens before the timer starts, so only the
            // RPC round trips are timed.
            switch (operation) {
                case BenchOperation::PUT:
                    keys = GeneratePutKeys(key_id, 1);
                    break;
                case BenchOperation::GET:
                    keys = GenerateGetKeys(key_id, 1);
                    break;
                case BenchOperation::BATCH_PUT:
                    keys = GeneratePutKeys(key_id, batch_size);
                    break;
                case BenchOperation::BATCH_GET:
                    keys = GenerateGetKeys(key_id, batch_size);
                    break;
            }
            auto start = std::chrono::steady_clock::now();
            switch (operation) {
                case BenchOperation::PUT:
                    outcome = Put(keys[0], slice_lengths[0], config);
                    break;
                case BenchOperation::GET:
                    outcome = Get(keys[0]);
                    break;
                case BenchOperation::BATCH_PUT:
                    outcome = BatchPut(keys, slice_lengths, config);
                    break;
                case BenchOperation::BATCH_GET:
                    outcome = BatchGet(keys);
                    break;
            }
            auto end = std::chrono::steady_clock::now();

            gCompletedOperations.fetch_add(outcome.ok);
            if (gMeasuring.load(std::memory_order_relaxed)) {
                gCalls.fetch_add(1, std::memory_order_relaxed);
                gKeysOk.fetch_add(outcome.ok, std::memory_order_relaxed);
                gKeysNotFound.fetch_add(outcome.not_found,
                                        std::memory_order_relaxed);
                gKeysErr.fetch_add(outcome.err, std::memory_order_relaxed);
                latencies_us.push_back(
                    std::chrono::duration<double, std::micro>(end - start)
                        .count());
            }
        }

        std::lock_guard<std::mutex> guard(gLatencyMutex);
        gLatenciesUs.insert(gLatenciesUs.end(), latencies_us.begin(),
                            latencies_us.end());
    }

    mooncake::MasterClient master_client_;

    std::atomic<bool> running_;
    std::vector<std::thread> threads_;
};

// Prefill "prefill_<i>" objects with several threads; returns objects filled.
static uint64_t PrefillObjects(uint64_t target_objects) {
    const uint64_t num_threads = std::max<uint64_t>(1, FLAGS_prefill_threads);
    std::atomic<uint64_t> filled_objects = 0;
    std::vector<std::thread> threads;
    for (uint64_t t = 0; t < num_threads; t++) {
        threads.emplace_back([&, t]() {
            unset_cpu_affinity();
            mooncake::MasterClient prefill_client(mooncake::generate_uuid());
            auto ec = prefill_client.Connect(FLAGS_master_server);
            if (ec != mooncake::ErrorCode::OK) {
                LOG(ERROR) << "Failed to connect prefill client";
                return;
            }
            const mooncake::ReplicateConfig config;
            const uint64_t begin = target_objects * t / num_threads;
            const uint64_t end = target_objects * (t + 1) / num_threads;
            uint64_t key_id = begin;
            while (key_id < end) {
                uint64_t batch = std::min(FLAGS_batch_size, end - key_id);
                std::vector<std::string> keys;
                std::vector<std::vector<uint64_t>> slice_lengths;
                keys.reserve(batch);
                slice_lengths.reserve(batch);
                for (uint64_t i = 0; i < batch; i++) {
                    keys.push_back("prefill_" + std::to_string(key_id++));
                    slice_lengths.push_back({FLAGS_value_size});
                }

                auto put_start_result =
                    prefill_client.BatchPutStart(keys, slice_lengths, config);
                std::vector<mooncake::ObjectMeta> object_metas;
                for (size_t i = 0; i < keys.size(); i++) {
                    if (put_start_result[i].has_value()) {
                        object_metas.emplace_back(
                            mooncake::ObjectMeta{keys[i], std::nullopt});
                    }
                }
                if (object_metas.empty()) {
                    continue;
                }
                auto put_end_result = prefill_client.BatchPutEnd(object_metas);
                for (auto& result : put_end_result) {
                    if (result.has_value()) {
                        filled_objects.fetch_add(1);
                    }
                }
            }
        });
    }
    for (auto& thread : threads) {
        thread.join();
    }
    return filled_objects.load();
}

static double Percentile(const std::vector<double>& sorted, double q) {
    if (sorted.empty()) {
        return 0.0;
    }
    size_t index = static_cast<size_t>(q * (sorted.size() - 1));
    return sorted[index];
}

struct PrefillInfo {
    uint64_t objects = 0;
    double seconds = 0.0;
    uint64_t master_rss_bytes = 0;
};

// Run one measurement point with `num_clients` clients and print a RESULT line.
static void RunPoint(BenchOperation operation, uint64_t num_clients,
                     uint64_t repeat, uint64_t index_keys_start,
                     const PrefillInfo& prefill) {
    std::vector<std::unique_ptr<BenchClient>> bench_clients;
    for (size_t i = 0; i < num_clients; i++) {
        bench_clients.emplace_back(
            std::make_unique<BenchClient>(FLAGS_master_server));
    }

    gMeasuring.store(false);
    gCalls = 0;
    gKeysOk = 0;
    gKeysNotFound = 0;
    gKeysErr = 0;
    {
        std::lock_guard<std::mutex> guard(gLatencyMutex);
        gLatenciesUs.clear();
    }

    auto num_threads = num_clients * FLAGS_num_threads;
    auto barrier = std::make_shared<std::latch>(num_threads +
                                                1);  // +1 for the main thread
    for (auto& bench_client : bench_clients) {
        bench_client->StartBench(barrier, operation, FLAGS_num_threads,
                                 FLAGS_batch_size, FLAGS_value_size,
                                 FLAGS_num_keys);
    }
    barrier->arrive_and_wait();
    LOG(INFO) << "Point clients=" << num_clients << " repeat=" << repeat
              << " started";

    std::this_thread::sleep_for(std::chrono::seconds(FLAGS_warmup));

    const uint64_t master_rss_start =
        FLAGS_master_pid > 0 ? ProcRssBytes(FLAGS_master_pid) : 0;
    const double master_cpu_start =
        FLAGS_master_pid > 0 ? ProcCpuSeconds(FLAGS_master_pid) : 0.0;
    const double self_cpu_start = ProcCpuSeconds(getpid());
    auto window_start = std::chrono::steady_clock::now();
    gMeasuring.store(true);

    uint64_t last_completed = gCompletedOperations.load();
    for (size_t i = 0; i < FLAGS_duration; i++) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        auto curr_completed = gCompletedOperations.load();
        LOG(INFO) << "Completed operations: "
                  << curr_completed - last_completed;
        last_completed = curr_completed;
    }

    gMeasuring.store(false);
    auto window_end = std::chrono::steady_clock::now();
    const double master_cpu_end =
        FLAGS_master_pid > 0 ? ProcCpuSeconds(FLAGS_master_pid) : 0.0;
    const double self_cpu_end = ProcCpuSeconds(getpid());

    for (auto& bench_client : bench_clients) {
        bench_client->StopBench();
    }
    bench_clients.clear();

    const double seconds =
        std::chrono::duration<double>(window_end - window_start).count();
    std::vector<double> latencies;
    {
        std::lock_guard<std::mutex> guard(gLatencyMutex);
        latencies.swap(gLatenciesUs);
    }
    std::sort(latencies.begin(), latencies.end());
    double mean_us = 0.0;
    for (double latency : latencies) {
        mean_us += latency;
    }
    if (!latencies.empty()) {
        mean_us /= latencies.size();
    }

    std::ostringstream json;
    json << std::fixed << std::setprecision(4);
    json << "{\"tag\":\"" << FLAGS_result_tag << "\""
         << ",\"operation\":\"" << FLAGS_operation << "\""
         << ",\"num_clients\":" << num_clients
         << ",\"num_threads\":" << FLAGS_num_threads
         << ",\"repeat\":" << FLAGS_repeat_offset + repeat << ",\"batch_size\":" << FLAGS_batch_size
         << ",\"value_size\":" << FLAGS_value_size
         << ",\"num_segments\":" << FLAGS_num_segments
         << ",\"segment_size\":" << FLAGS_segment_size
         << ",\"prefill_objects\":" << prefill.objects
         << ",\"prefill_seconds\":" << prefill.seconds
         << ",\"master_rss_after_prefill\":" << prefill.master_rss_bytes
         << ",\"index_keys_start\":" << index_keys_start
         << ",\"warmup_s\":" << FLAGS_warmup << ",\"measured_s\":" << seconds
         << ",\"calls\":" << gCalls.load() << ",\"keys_ok\":" << gKeysOk.load()
         << ",\"keys_not_found\":" << gKeysNotFound.load()
         << ",\"keys_err\":" << gKeysErr.load()
         << ",\"keys_per_s\":" << gKeysOk.load() / seconds
         << ",\"calls_per_s\":" << gCalls.load() / seconds
         << ",\"lat_mean_ms\":" << mean_us / 1000.0
         << ",\"lat_p50_ms\":" << Percentile(latencies, 0.50) / 1000.0
         << ",\"lat_p90_ms\":" << Percentile(latencies, 0.90) / 1000.0
         << ",\"lat_p99_ms\":" << Percentile(latencies, 0.99) / 1000.0
         << ",\"lat_p999_ms\":" << Percentile(latencies, 0.999) / 1000.0
         << ",\"lat_max_ms\":"
         << (latencies.empty() ? 0.0 : latencies.back() / 1000.0)
         << ",\"master_cpu_pct\":"
         << (FLAGS_master_pid > 0
                 ? 100.0 * (master_cpu_end - master_cpu_start) / seconds
                 : -1.0)
         << ",\"client_cpu_pct\":"
         << 100.0 * (self_cpu_end - self_cpu_start) / seconds
         << ",\"master_rss_start\":" << master_rss_start << "}";
    std::cout << "RESULT " << json.str() << std::endl;
}

static std::vector<uint64_t> ParseClientsList() {
    std::vector<uint64_t> counts;
    std::stringstream ss(FLAGS_clients_list);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (!item.empty()) {
            counts.push_back(std::stoull(item));
        }
    }
    if (counts.empty()) {
        counts.push_back(FLAGS_num_clients);
    }
    return counts;
}

int main(int argc, char** argv) {
    std::vector<std::unique_ptr<SegmentClient>> segment_clients;
    std::mutex segment_clients_mutex;
    std::jthread ping_thread;

    google::InitGoogleLogging("MasterBench");
    FLAGS_logtostderr = true;

    gflags::ParseCommandLineFlags(&argc, &argv, false);

    ping_thread = std::jthread([&](std::stop_token stop_token) {
        static const auto OneSecond = std::chrono::seconds(1);

        unset_cpu_affinity();

        while (!stop_token.stop_requested()) {
            std::chrono::nanoseconds time_elapsed;
            auto start_time = std::chrono::steady_clock::now();
            {
                std::lock_guard<std::mutex> guard(segment_clients_mutex);
                for (auto& segment_client : segment_clients) {
                    segment_client->Ping();
                }
            }
            time_elapsed = std::chrono::steady_clock::now() - start_time;

            if (OneSecond > time_elapsed) {
                std::this_thread::sleep_for(OneSecond - time_elapsed);
            }
        }
    });

    LOG(INFO) << "Mounting " << FLAGS_num_segments << " segments...";
    for (size_t i = 0; i < FLAGS_num_segments; i++) {
        auto segment_client = std::make_unique<SegmentClient>(
            "segment_client_" + std::to_string(i), FLAGS_master_server,
            kSegmentBase + i * FLAGS_segment_size, FLAGS_segment_size);
        {
            std::lock_guard<std::mutex> guard(segment_clients_mutex);
            segment_clients.push_back(std::move(segment_client));
        }
    }
    LOG(INFO) << "Segments mounted";

    // Prefill segments if requested
    PrefillInfo prefill;
    uint64_t target_objects = FLAGS_prefill_objects;
    if (target_objects == 0 && FLAGS_prefill_ratio > 0.0) {
        uint64_t total_capacity = FLAGS_num_segments * FLAGS_segment_size;
        uint64_t target_bytes =
            static_cast<uint64_t>(total_capacity * FLAGS_prefill_ratio);
        target_objects = target_bytes / FLAGS_value_size;
    }
    if (target_objects > 0) {
        LOG(INFO) << "Prefilling " << target_objects << " objects with "
                  << FLAGS_prefill_threads << " threads...";
        auto prefill_start = std::chrono::steady_clock::now();
        prefill.objects = PrefillObjects(target_objects);
        prefill.seconds = std::chrono::duration<double>(
                              std::chrono::steady_clock::now() - prefill_start)
                              .count();
        if (FLAGS_master_pid > 0) {
            prefill.master_rss_bytes = ProcRssBytes(FLAGS_master_pid);
        }
        gPrefillCount = target_objects;
        LOG(INFO) << "Prefill completed: " << prefill.objects << " objects in "
                  << prefill.seconds << " s";
        if (prefill.objects != target_objects) {
            LOG(WARNING) << "Prefill incomplete: " << prefill.objects << "/"
                         << target_objects;
        }
    }

    const auto operation = ParseOperation(FLAGS_operation);
    const bool is_put = operation == BenchOperation::PUT ||
                        operation == BenchOperation::BATCH_PUT;
    uint64_t index_keys = prefill.objects;
    bool first_point = true;
    for (uint64_t num_clients : ParseClientsList()) {
        for (uint64_t repeat = 0; repeat < FLAGS_repeats; repeat++) {
            if (!first_point && FLAGS_pause > 0) {
                std::this_thread::sleep_for(std::chrono::seconds(FLAGS_pause));
            }
            first_point = false;
            RunPoint(operation, num_clients, repeat, index_keys, prefill);
            if (is_put) {
                // Writes made during warmup are not counted in keys_ok, so
                // this is a lower bound on the index size.
                index_keys += gKeysOk.load();
            }
        }
    }

    LOG(INFO) << "Stopping ping thread...";
    if (ping_thread.joinable()) {
        ping_thread.request_stop();
        ping_thread.join();
    }
    LOG(INFO) << "Ping thread stopped";

    LOG(INFO) << "Disconnecting from master...";
    segment_clients.clear();
    LOG(INFO) << "Disconnected from master";

    google::ShutdownGoogleLogging();

    return 0;
}
