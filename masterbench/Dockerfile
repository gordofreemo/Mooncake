# Build image for the masterbench experiment: mooncake_master plus the
# patched master_bench, CPU-only (no CUDA, no RDMA devices needed).
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates git sudo \
        python3 python3-matplotlib python3-numpy python3-pandas python3-seaborn util-linux procps && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY . /workspace

# Upstream dependency script: apt packages, git submodules, Go.
RUN bash dependencies.sh -y && rm -rf /var/lib/apt/lists/*

# Release build of only the two binaries the experiment needs. The build tree
# is kept so the binaries' build-tree RPATHs stay valid.
RUN mkdir -p build && cd build && \
    cmake -G Ninja .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_UNIT_TESTS=OFF \
        -DBUILD_BENCHMARK=ON \
        -DUSE_CUDA=OFF \
        -DUSE_ETCD=OFF \
        -DSTORE_USE_ETCD=OFF \
        -DWITH_STORE_RUST=OFF \
        -DWITH_RUST_EXAMPLE=OFF \
        -DWITH_EP=OFF && \
    cmake --build . --target mooncake_master master_bench && \
    ln -sf "$(find /workspace/build -type f -name mooncake_master -perm -u+x | head -1)" /usr/local/bin/mooncake_master && \
    ln -sf "$(find /workspace/build -type f -name master_bench -perm -u+x | head -1)" /usr/local/bin/master_bench && \
    mooncake_master --help >/dev/null 2>&1; master_bench --help >/dev/null 2>&1; \
    test -x /usr/local/bin/mooncake_master && test -x /usr/local/bin/master_bench

COPY masterbench/condor/run_sweep.sh /usr/local/bin/run_sweep.sh
COPY masterbench/plot.py /usr/local/bin/masterbench_plot.py
RUN chmod +x /usr/local/bin/run_sweep.sh
