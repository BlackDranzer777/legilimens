ARG BASE_IMAGE
FROM ${BASE_IMAGE}
WORKDIR /src
ARG TRANSPORT_SHA
ARG WAITING_ROOM_SHA
COPY lifecycle.patch /tmp/lifecycle.patch
RUN sed -i 's/\r$//' /tmp/lifecycle.patch videocall-transport/src/webtransport.rs dioxus-ui/src/components/waiting_room.rs
RUN printf '%s  %s\n' "$TRANSPORT_SHA" videocall-transport/src/webtransport.rs "$WAITING_ROOM_SHA" dioxus-ui/src/components/waiting_room.rs | sha256sum --check
RUN git apply --check /tmp/lifecycle.patch
RUN git apply /tmp/lifecycle.patch
WORKDIR /src/dioxus-ui
RUN trunk build --locked --offline
LABEL org.legilimens.purpose="experimental-videocall-lifecycle-fixture"
