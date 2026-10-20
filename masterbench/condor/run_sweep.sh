#!/bin/bash
# Runs one sweep of the masterbench experiment inside the container.
#   run_sweep.sh BatchGet|BatchPut OUTDIR
# Lookups: one master, prefilled once, all points in one master_bench process.
# Writes: a fresh master and prefill for every point.
set -euo pipefail

OP=$1
OUT=$2
CLIENTS=${CLIENTS:-1,2,4,8,16,32,64,128}
REPEATS=${REPEATS:-3}
DURATION=${DURATION:-60}
WARMUP=${WARMUP:-5}
PAUSE=${PAUSE:-10}
PREFILL=${PREFILL:-10000000}
PREFILL_THREADS=${PREFILL_THREADS:-16}
MASTER_CORES=${MASTER_CORES:-0-15}
RPC_THREADS=${RPC_THREADS:-16}
NCPU=$(nproc --all)
CLIENT_CORES=${CLIENT_CORES:-16-$((NCPU - 1))}
SEGMENTS=${SEGMENTS:-32}
SEGMENT_SIZE=${SEGMENT_SIZE:-$((2 * 1024 * 1024 * 1024 * 1024))}
PORT=${PORT:-50051}

mkdir -p "$OUT"
RESULTS="$OUT/results.jsonl"
# Logs are written to local disk and copied to $OUT after each point, so a
# stall of the shared home file system cannot block the master or the bench.
LOCAL=${_CONDOR_SCRATCH_DIR:-/tmp}/masterbench
mkdir -p "$LOCAL"
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
{
    echo "host=$(hostname) date=$(date -Is) op=$OP"
    echo "clients=$CLIENTS repeats=$REPEATS duration=$DURATION warmup=$WARMUP prefill=$PREFILL"
    echo "master_cores=$MASTER_CORES rpc_threads=$RPC_THREADS client_cores=$CLIENT_CORES"
    echo "segments=$SEGMENTS segment_size=$SEGMENT_SIZE store_counts=${STORE_COUNTS:-} active=${ACTIVE:-}"
    echo "nofile=$(ulimit -n) hard=$(ulimit -Hn)"
    echo "fast_put=${MOONCAKE_MASTERBENCH_FAST_PUT:-0}"
    lscpu | grep -E "Model name|Socket|Core|Thread|NUMA node" || true
    free -g | head -2 || true
} > "$OUT/env.txt"

MPID=""
MTAG=""
start_master() {
    MTAG=$1
    taskset -c "$MASTER_CORES" mooncake_master \
        --port="$PORT" \
        --rpc_thread_num="$RPC_THREADS" \
        > "$LOCAL/master_$1.log" 2>&1 &
    MPID=$!
    for _ in $(seq 1 120); do
        if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
            sleep 1
            echo "$1 $(awk '/VmRSS/ {print $2 * 1024}' "/proc/$MPID/status")" >> "$OUT/master_rss_idle.txt"
            return 0
        fi
        sleep 0.5
    done
    echo "master did not start" >&2
    exit 1
}

stop_master() {
    if [ -n "$MPID" ]; then
        kill "$MPID" 2>/dev/null || true
        wait "$MPID" 2>/dev/null || true
        MPID=""
        cp "$LOCAL/master_$MTAG.log" "$OUT/" 2>/dev/null || true
        sleep 2
    fi
}
trap 'stop_stress; stop_master' EXIT

bench() {
    local tag=$1
    local operation=$2
    local segments=$3
    local segment_size=$4
    shift 4
    taskset -c "$CLIENT_CORES" master_bench \
        --master_server="127.0.0.1:$PORT" \
        --operation="$operation" \
        --num_segments="$segments" \
        --segment_size="$segment_size" \
        --value_size=4096 \
        --batch_size=128 \
        --num_threads=1 \
        --duration="$DURATION" \
        --warmup="$WARMUP" \
        --keep_cpu_affinity=true \
        --get_from_prefill=true \
        --prefill_objects="$PREFILL" \
        --prefill_threads="$PREFILL_THREADS" \
        --master_pid="$MPID" \
        --result_tag="$tag" \
        "$@" 2>>"$LOCAL/bench_$tag.log" > "$LOCAL/stdout_$tag.log" ||
        echo "master_bench $tag exited with $?" >> "$OUT/env.txt"
    collect "$tag"
}

# Copy a point's logs to $OUT and append its RESULT and TIMELINE lines.
collect() {
    local tag=$1
    cp "$LOCAL/bench_$tag.log" "$LOCAL/stdout_$tag.log" "$OUT/" 2>/dev/null || true
    sed -n 's/^RESULT //p' "$LOCAL/stdout_$tag.log" >> "$RESULTS"
    sed -n 's/^TIMELINE //p' "$LOCAL/stdout_$tag.log" >> "$OUT/timeline.jsonl"
}

# Largest calls_per_s among the RESULT lines tagged $1.
max_calls_per_s() {
    python3 - "$RESULTS" "$1" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print(max(r["calls_per_s"] for r in rows if r["tag"] == sys.argv[2]))
PY
}

# Start stress-ng with $1 CPU workers per core on cores $2; sets SPID.
SPID=""
start_stress() {
    local per_core=$1 cores=$2 n
    n=$(taskset -c "$cores" nproc)
    SPID=""
    if [ "$per_core" -gt 0 ]; then
        stress-ng --cpu $((per_core * n)) --cpu-method int64 --taskset "$cores" \
            --quiet > "$LOCAL/stress.log" 2>&1 &
        SPID=$!
        sleep 2
    fi
}
stop_stress() {
    if [ -n "$SPID" ]; then
        kill -TERM "$SPID" 2>/dev/null || true
        wait "$SPID" 2>/dev/null || true
        SPID=""
    fi
}

case "$OP" in
    BatchGet)
        start_master get
        bench "get" BatchGet "$SEGMENTS" "$SEGMENT_SIZE" --clients_list="$CLIENTS" --repeats="$REPEATS" --pause="$PAUSE"
        stop_master
        ;;
    BatchPut)
        IFS=',' read -ra COUNTS <<< "$CLIENTS"
        for n in "${COUNTS[@]}"; do
            for r in $(seq 0 $((REPEATS - 1))); do
                start_master "put_n${n}_r${r}"
                bench "put_n${n}_r${r}" BatchPut "$SEGMENTS" "$SEGMENT_SIZE" --clients_list="$n" --repeats=1 --repeat_offset="$r"
                stop_master
            done
        done
        ;;
    FanGet | FanPut)
        # E2: vary the number of storage clients (each mounts one segment and
        # pings once per second) at a fixed active load; total pool size fixed.
        POOL=${POOL:-$((64 * 1024 * 1024 * 1024 * 1024))}
        ACTIVE=${ACTIVE:-16}
        IFS=',' read -ra STORES <<< "${STORE_COUNTS:-32,128,512,2048,8192}"
        for s in "${STORES[@]}"; do
            size=$((POOL / s))
            if [ "$OP" = FanGet ]; then
                start_master "fanget_s${s}"
                bench "fanget_s${s}" BatchGet "$s" "$size" --clients_list="$ACTIVE" \
                    --repeats="$REPEATS" --pause="$PAUSE"
                stop_master
            else
                for r in $(seq 0 $((REPEATS - 1))); do
                    start_master "fanput_s${s}_r${r}"
                    bench "fanput_s${s}_r${r}" BatchPut "$s" "$size" --clients_list="$ACTIVE" \
                        --repeats=1 --repeat_offset="$r"
                    stop_master
                done
            fi
        done
        ;;
    Evict)
        # E3: steady state with a full pool. RESIDENT objects of VALUE_SIZE
        # fill the pool to FILL (the eviction watermark is 0.90); writers then
        # add new keys, so the pool stays at the watermark and eviction runs
        # continuously. Mixed lookups read the most recent RESIDENT/10 keys.
        VALUE_SIZE=${VALUE_SIZE:-1048576}
        FILL=${FILL:-0.85}
        ACTIVE=${ACTIVE:-16}
        MIXED=${MIXED:-16}
        IFS=',' read -ra RESIDENTS <<< "${RESIDENT:-1000000,10000000}"
        for res in "${RESIDENTS[@]}"; do
            size=$(python3 -c "import math; print(math.ceil($res * $VALUE_SIZE / $FILL / $SEGMENTS / 2**20) * 2**20)")
            start_master "evict_r${res}"
            bench "evict_r${res}" BatchPut "$SEGMENTS" "$size" --clients_list="$ACTIVE" \
                --repeats=1 --value_size="$VALUE_SIZE" --prefill_objects="$res" \
                --mixed_get_clients="$MIXED" --get_recent_window=$((res / 10)) \
                --timeline=true
            stop_master
        done
        ;;
    OpenGet | OpenPut)
        # E5: Poisson arrivals at fractions of the closed-loop capacity, which
        # is calibrated first on the same node (best of CAL_CLIENTS).
        LOADS=${LOADS:-0.1,0.3,0.5,0.7,0.8,0.9,0.95}
        OPEN_CLIENTS=${OPEN_CLIENTS:-256}
        CAL_CLIENTS=${CAL_CLIENTS:-16,32,64,128}
        op=BatchGet
        if [ "$OP" = OpenPut ]; then op=BatchPut; fi
        start_master "${OP}_cal"
        bench "${OP}_cal" "$op" "$SEGMENTS" "$SEGMENT_SIZE" --clients_list="$CAL_CLIENTS" --repeats=1 \
            --duration="${CAL_DURATION:-15}" --warmup=2
        stop_master
        cap=$(max_calls_per_s "${OP}_cal")
        echo "capacity_calls_per_s=$cap" >> "$OUT/env.txt"
        IFS=',' read -ra FRACS <<< "$LOADS"
        for f in "${FRACS[@]}"; do
            rate=$(python3 -c "print($cap * $f)")
            if [ "$OP" = OpenGet ]; then
                start_master "openget_f${f}"
                bench "openget_f${f}" BatchGet "$SEGMENTS" "$SEGMENT_SIZE" --clients_list="$OPEN_CLIENTS" \
                    --repeats="$REPEATS" --pause="$PAUSE" --open_loop_rate="$rate"
                stop_master
            else
                for r in $(seq 0 $((REPEATS - 1))); do
                    start_master "openput_f${f}_r${r}"
                    bench "openput_f${f}_r${r}" BatchPut "$SEGMENTS" "$SEGMENT_SIZE" --clients_list="$OPEN_CLIENTS" \
                        --repeats=1 --repeat_offset="$r" --open_loop_rate="$rate"
                    stop_master
                done
            fi
        done
        ;;
    Contend)
        # E4: open-loop lookups at a fixed RATE (calls/s) while stress-ng runs
        # LEVEL CPU workers per core on the client cores or the master cores.
        RATE=${RATE:-5000}
        IFS=',' read -ra LEVEL_LIST <<< "${LEVELS:-0,1,2,4}"
        IFS=',' read -ra SIDE_LIST <<< "${SIDES:-client,master}"
        for side in "${SIDE_LIST[@]}"; do
            cores=$CLIENT_CORES
            if [ "$side" = master ]; then cores=$MASTER_CORES; fi
            for k in "${LEVEL_LIST[@]}"; do
                tag="contend_${side}_k${k}"
                start_master "$tag"
                # Load the cores only once segments are mounted and the keys
                # preloaded, i.e. from the first point's warm-up on.
                bench "$tag" BatchGet "$SEGMENTS" "$SEGMENT_SIZE" --clients_list="${ACTIVE:-16}" \
                    --repeats="$REPEATS" --pause="$PAUSE" --open_loop_rate="$RATE" &
                BPID=$!
                for _ in $(seq 1 600); do
                    grep -q "Point clients=" "$LOCAL/bench_$tag.log" 2>/dev/null && break
                    sleep 1
                done
                start_stress "$k" "$cores"
                wait "$BPID" || true
                stop_stress
                stop_master
            done
        done
        ;;
    *)
        echo "unknown op $OP" >&2
        exit 2
        ;;
esac
echo "done $(date -Is)" >> "$OUT/env.txt"
