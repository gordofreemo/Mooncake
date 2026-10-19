#!/bin/bash
# Submit masterbench sweeps on Conduit (run from the repo checkout on conduit).
#   submit.sh smoke|full
# smoke: short lookup + write runs on magni1. full: lookup sweep on magni1 and
# write sweep on magni2 (identical nodes, single-node experiments each).
set -euo pipefail
MODE=${1:-smoke}
HERE=$(cd "$(dirname "$0")" && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
OUT=$HERE/../results/$MODE-$STAMP
mkdir -p "$OUT"

if [ "$MODE" = fanin-smoke ]; then
    ENV="STORE_COUNTS=32,8192 ACTIVE=16 REPEATS=1 DURATION=10 WARMUP=2 PAUSE=2 PREFILL=200000"
    JOBS="FanGet magni1 fanget
FanPut magni1 fanput"
elif [ "$MODE" = fanin ]; then
    ENV="STORE_COUNTS=32,128,512,2048,8192 ACTIVE=16 REPEATS=3 DURATION=60 WARMUP=5 PAUSE=10 PREFILL=10000000"
    JOBS="FanGet magni1 fanget
FanPut magni2 fanput"
elif [ "$MODE" = smoke ]; then
    ENV="CLIENTS=1,8 REPEATS=1 DURATION=10 WARMUP=2 PAUSE=2 PREFILL=200000"
    JOBS="BatchGet magni1 get
BatchPut magni1 put"
else
    ENV="CLIENTS=1,2,4,8,16,32,64,128 REPEATS=3 DURATION=60 WARMUP=5 PAUSE=10 PREFILL=10000000"
    JOBS="BatchGet magni1 get
BatchPut magni2 put"
fi

while read -r op node name; do
    mkdir -p "$OUT/$name"
    cat > "$OUT/$name/job.sub" <<SUB
universe       = docker
docker_image   = ghcr.io/gordofreemo/mooncake-masterbench:292e839
+WantGPUHomeMounted = true
executable     = $HERE/run_sweep.sh
arguments      = $op $OUT/$name
environment    = "$ENV"
requirements   = (Machine == "$node.hpc.uni-saarland.de")
request_cpus   = ${REQUEST_CPUS:-192}
request_memory = 256GB
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
