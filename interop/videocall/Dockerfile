# Test-only build environment, not a release image. Upstream source stays unchanged.
FROM rust:1.93.1-bookworm@sha256:7c4ae649a84014c467d79319bbf17ce2632ae8b8be123ac2fb2ea5be46823f31 AS backend
RUN apt-get update && apt-get install -y --no-install-recommends cmake clang protobuf-compiler && rm -rf /var/lib/apt/lists/*
WORKDIR /src
ADD source.tar /src/
ENV CARGO_BUILD_JOBS=2 CARGO_PROFILE_DEV_DEBUG=0
RUN cargo build --locked -p videocall-api --bin webtransport_server -p meeting-api --bin meeting-api
LABEL org.legilimens.purpose="local-videocall-interop"

# Frontend: build the real upstream Dioxus/WASM UI + its WebCodecs/NetEq workers with
# Trunk. Same pinned Rust base; source unchanged. wasm-bindgen is 0.2.108 in the lock.
FROM rust:1.93.1-bookworm@sha256:7c4ae649a84014c467d79319bbf17ce2632ae8b8be123ac2fb2ea5be46823f31 AS frontend
RUN apt-get update && apt-get install -y --no-install-recommends cmake clang protobuf-compiler binaryen ca-certificates curl && rm -rf /var/lib/apt/lists/*
RUN rustup target add wasm32-unknown-unknown
RUN cargo install --locked trunk@0.21.14 && cargo install --locked wasm-bindgen-cli@0.2.108
WORKDIR /src
ADD source.tar /src/
WORKDIR /src/dioxus-ui
ENV CARGO_BUILD_JOBS=2 CARGO_PROFILE_DEV_DEBUG=0
# Trunk compiles the app plus the worker_decoder and neteq_worker WASM modules and
# copies static assets, config.js and the encoder/decoder worker scripts into dist/.
RUN trunk build --locked

# Export only the built static assets (no toolchain) for host-side serving.
FROM scratch AS dist
COPY --from=frontend /src/dioxus-ui/dist /
