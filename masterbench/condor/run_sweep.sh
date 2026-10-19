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
ulimit -n "$(ulimit -Hn)" 2>/dev/null || true
{
    echo "host=$(hostname) date=$(date -Is) op=$OP"
    echo "clients=$CLIENTS repeats=$REPEATS duration=$DURATION warmup=$WARMUP prefill=$PREFILL"
    echo "master_cores=$MASTER_CORES rpc_threads=$RPC_THREADS client_cores=$CLIENT_CORES"
    echo "segments=$SEGMENTS segment_size=$SEGMENT_SIZE store_counts=${STORE_COUNTS:-} active=${ACTIVE:-}"
    echo "nofile=$(ulimit -n) hard=$(ulimit -Hn)"
    lscpu | grep -E "Model name|Socket|Core|Thread|NUMA node" || true
    free -g | head -2 || true
} > "$OUT/env.txt"

MPID=""
start_master() {
    taskset -c "$MASTER_CORES" mooncake_master \
        --port="$PORT" \
        --rpc_thread_num="$RPC_THREADS" \
        > "$OUT/master_$1.log" 2>&1 &
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
        sleep 2
    fi
}
trap stop_master EXIT

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
        "$@" 2>>"$OUT/bench_$tag.log" |
        sed -n 's/^RESULT //p' >> "$RESULTS"
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
    *)
        echo "unknown op $OP" >&2
        exit 2
        ;;
esac
echo "done $(date -Is)" >> "$OUT/env.txt"
