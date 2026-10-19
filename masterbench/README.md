# masterbench: how much metadata traffic can one Mooncake master serve?

Experiment E1 for the DPU-resident metadata manager work: capacity and latency of
the Mooncake Store master (the centralized, in-memory metadata service) on one node.
Based on upstream commit `292e839`.

## What is measured

- One `mooncake_master` pinned to 16 cores (the BlueField-3 core count; these are Xeon
  cores, so treat results as an upper bound for a DPU).
- `master_bench` clients on the other cores of the same node, each with one request in
  flight (128 keys per request). Segments are fake address ranges, so no payload moves:
  only metadata operations are measured.
- 10M keys preloaded, 4 KB placeholder objects, 32 fake storage nodes x 2 TB (no eviction).
- Client counts 1..128 (powers of two), 60 s per point after 5 s warmup, 3 repeats.
- Lookups (`BatchGetReplicaList`, keys uniform over the 10M) share one master; every
  write run (`BatchPutStart` + `BatchPutEnd`) gets a fresh master and preload.
- Per point: keys/s, p50/p90/p99 latency, master and client CPU, master RSS,
  found / not-found / error counts.

## Changes to `master_bench.cpp` (all behind flags; defaults keep upstream behavior)

`--prefill_objects`, `--prefill_threads`, `--get_from_prefill`, `--keep_cpu_affinity`,
`--warmup`, `--clients_list`, `--repeats`, `--repeat_offset`, `--pause`, `--master_pid`,
`--result_tag`; per-call latency recording; found / not-found / error counts; one
`RESULT {json}` line per point.

## Running on Conduit

```bash
git clone -b masterbench https://github.com/gordofreemo/Mooncake.git
cd Mooncake/masterbench/condor
./submit.sh smoke   # short check on magni1
./submit.sh full    # lookups on magni1, writes on magni2
```

The image `ghcr.io/gordofreemo/mooncake-masterbench:292e839` is built by
`.github/workflows/masterbench.yml`. Plots: `python3 masterbench/plot.py <results dirs>`
(inside the image: `masterbench_plot.py`).
