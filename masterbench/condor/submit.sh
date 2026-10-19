#!/bin/bash
# Submit masterbench sweeps on Conduit (run from the repo checkout on conduit).
#   submit.sh smoke|full|fanin-smoke|fanin|fanin-fast-smoke|fanin-fast|
#             e345-smoke|evict|evict-fast|open|contend
# smoke: short lookup + write runs on magni1. full: lookup sweep on magni1 and
# write sweep on magni2 (identical nodes, single-node experiments each).
# fanin-fast*: the fanin sweeps with MOONCAKE_MASTERBENCH_FAST_PUT=1 (PutStart
# without per-key work proportional to the number of segments or clients).
# evict: E3, full pool with eviction (evict-fast: patched master).
# open: E5, open-loop lookups and writes. contend: E4, lookups under stress-ng.
# e345-smoke: short versions of E3 (patched), E4 and E5 on one node.
# Each JOBS line is "op node name [extra environment]".
# IMAGE, REQUEST_CPUS and REQUEST_MEMORY override the job settings.
set -euo pipefail
MODE=${1:-smoke}
HERE=$(cd "$(dirname "$0")" && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
OUT=$HERE/../results/$MODE-$STAMP
IMAGE=${IMAGE:-ghcr.io/gordofreemo/mooncake-masterbench:292e839}
mkdir -p "$OUT"
echo "$IMAGE" > "$OUT/image.txt"

FAST=""
BASE=$MODE
FASTENV=" MOONCAKE_MASTERBENCH_FAST_PUT=1"
if [[ "$MODE" == fanin-fast* ]]; then
    FAST=" MOONCAKE_MASTERBENCH_FAST_PUT=1"
    BASE=${MODE/fanin-fast/fanin}
fi

if [ "$BASE" = e345-smoke ]; then
    ENV="REPEATS=1 PAUSE=2 PREFILL=200000"
    JOBS="Evict ${NODE1:-magni1} evict SEGMENTS=128 RESIDENT=200000 DURATION=20 WARMUP=0$FASTENV
OpenGet ${NODE1:-magni1} openget SEGMENTS=32 LOADS=0.5,0.9 CAL_CLIENTS=64 CAL_DURATION=5 DURATION=10 WARMUP=2$FASTENV
OpenPut ${NODE1:-magni1} openput SEGMENTS=32 LOADS=0.5,0.9 CAL_CLIENTS=64 CAL_DURATION=5 DURATION=10 WARMUP=2$FASTENV
Contend ${NODE1:-magni1} contend SEGMENTS=32 CLIENT_CORES=16-31 LEVELS=0,2 DURATION=10 WARMUP=2$FASTENV"
elif [ "$BASE" = evict ] || [ "$BASE" = evict-fast ]; then
    ENV="SEGMENTS=128 ACTIVE=16 MIXED=16 DURATION=180 WARMUP=0 PAUSE=0 REPEATS=1"
    if [ "$BASE" = evict ]; then
        JOBS="Evict ${NODE1:-magni1} evict RESIDENT=1000000,10000000"
    else
        JOBS="Evict ${NODE1:-magni1} evict RESIDENT=1000000,10000000,100000000$FASTENV"
    fi
elif [ "$BASE" = open ]; then
    ENV="SEGMENTS=32 REPEATS=3 DURATION=60 WARMUP=5 PAUSE=5"
    JOBS="OpenGet ${NODE1:-magni2} openget PREFILL=10000000$FASTENV
OpenPut ${NODE1:-magni2} openput PREFILL=1000000$FASTENV"
elif [ "$BASE" = contend ]; then
    ENV="SEGMENTS=32 REPEATS=3 DURATION=30 WARMUP=5 PAUSE=5 PREFILL=10000000 CLIENT_CORES=16-31 RATE=5000"
    JOBS="Contend ${NODE1:-magni2} contend$FASTENV"
elif [ "$BASE" = fanin-smoke ]; then
    ENV="STORE_COUNTS=32,8192 ACTIVE=16 REPEATS=1 DURATION=10 WARMUP=2 PAUSE=2 PREFILL=200000"
    JOBS="FanGet magni1 fanget
FanPut magni1 fanput"
elif [ "$BASE" = fanin ]; then
    ENV="STORE_COUNTS=32,128,512,2048,8192 ACTIVE=16 REPEATS=3 DURATION=60 WARMUP=5 PAUSE=10 PREFILL=1000000"
    JOBS="FanGet magni1 fanget
FanPut magni2 fanput"
elif [ "$BASE" = smoke ]; then
    ENV="CLIENTS=1,8 REPEATS=1 DURATION=10 WARMUP=2 PAUSE=2 PREFILL=200000"
    JOBS="BatchGet magni1 get
BatchPut magni1 put"
else
    ENV="CLIENTS=1,2,4,8,16,32,64,128 REPEATS=3 DURATION=60 WARMUP=5 PAUSE=10 PREFILL=10000000"
    JOBS="BatchGet magni1 get
BatchPut magni2 put"
fi
ENV="$ENV$FAST"

while read -r op node name extra; do
    mkdir -p "$OUT/$name"
    cat > "$OUT/$name/job.sub" <<SUB
universe       = docker
docker_image   = $IMAGE
+WantGPUHomeMounted = true
executable     = $HERE/run_sweep.sh
arguments      = $op $OUT/$name
environment    = "$ENV${extra:+ $extra}"
requirements   = (Machine == "$node.hpc.uni-saarland.de")
request_cpus   = ${REQUEST_CPUS:-192}
request_memory = ${REQUEST_MEMORY:-256GB}
request_disk   = 20GB
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
output = $OUT/$name/job.out
error  = $OUT/$name/job.err
log    = $OUT/$name/job.log
queue
SUB
    condor_submit "$OUT/$name/job.sub"
done <<< "$JOBS"
echo "$OUT"
