# RioServer NG: music server and network-boot server for the Rio Receiver /
# Dell Digital Audio Receiver.
#
#   docker build -t rioserver-ng .
#
# Stage 1 cross-compiles the receiver firmware; stage 2 is the server.

# The FFmpeg that shairport-sync is built with and the equaliser's ffmpeg.
ARG FFMPEG_COMMIT=3a0867c2bfda4a4d4309ca1a8cbdc6175e67f587

# Ubuntu 24.04 ships arm-none-eabi-gcc 13.2, the compiler the firmware is tested with.
# The firmware runs on the receiver, not the server, so this stage always runs
# natively on the build machine, even when building an arm64 server image.
FROM --platform=$BUILDPLATFORM ubuntu:24.04 AS firmware
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gcc-arm-none-eabi make python3 ca-certificates zopfli \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src/firmware
COPY firmware/ ./
# Fetches libmad and the RioPlay kernel image from SourceForge (checksummed),
# and swaps the kernel's DELL/Rio splash screens for ours (needs zopfli).
RUN make clean && make \
 && mkdir -p /out \
 && cp build/receiver-ng.arf build/deps/receiver-rioplay.arf /out/

# AirPlay and Spotify Connect (off unless switched on per receiver in the web
# interface). Both are built on the build machine for the image's
# architecture (cross-compiling, not under emulation, which would take far
# longer for the arm64 image), from pinned commits, on the same Debian
# release as the final image.

# librespot (Spotify Connect), v0.8.0. Rust's own TLS (rustls with Mozilla's
# roots) and mDNS (libmdns) keep it free of C libraries.
FROM --platform=$BUILDPLATFORM rust:1.91-slim-trixie AS librespot
ARG TARGETARCH
ARG LIBRESPOT_COMMIT=d36f9f1907e8cc9d68a93f8ebc6b627b1bf7267d
# Its login endpoint binds [::] only, which fails on hosts with IPv6 off.
COPY docker/librespot-ipv4-fallback.patch /tmp/
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) gnu=x86_64-linux-gnu; rt=x86_64-unknown-linux-gnu ;; \
      arm64) gnu=aarch64-linux-gnu; rt=aarch64-unknown-linux-gnu ;; \
      *) echo "unsupported architecture $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    pkgs="git ca-certificates"; \
    if [ "$(dpkg --print-architecture)" != "$TARGETARCH" ]; then \
      pkgs="$pkgs gcc-$(echo $gnu | tr _ -) libc6-dev-$TARGETARCH-cross"; \
    fi; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $pkgs; \
    rm -rf /var/lib/apt/lists/*; \
    rustup target add "$rt"; \
    mkdir /src; cd /src; git init -q; \
    git fetch -q --depth 1 https://github.com/librespot-org/librespot.git "$LIBRESPOT_COMMIT"; \
    git checkout -q FETCH_HEAD; \
    git apply /tmp/librespot-ipv4-fallback.patch; \
    var=$(echo "$rt" | tr 'a-z-' 'A-Z_'); \
    export "CARGO_TARGET_${var}_LINKER=$gnu-gcc" "CC_$(echo $rt | tr - _)=$gnu-gcc"; \
    cargo build --release --locked --target "$rt" --no-default-features \
      --features rustls-tls-webpki-roots,with-libmdns; \
    mkdir -p /out; cp "target/$rt/release/librespot" /out/; "$gnu-strip" /out/librespot; \
    rm -rf /src /usr/local/cargo/registry

# shairport-sync (classic AirPlay), 5.6.2, with a minimal static FFmpeg (only
# the decoders AirPlay needs) instead of the distribution's full one, which
# would add well over 100 MB. It writes audio to stdout and metadata to a
# pipe, and advertises itself through `avahi-publish-service`, which the
# server provides (see server/rioserver/speakers.py): no daemons needed.
FROM --platform=$BUILDPLATFORM debian:trixie-slim AS shairport
ARG TARGETARCH
ARG FFMPEG_COMMIT
ARG SHAIRPORT_COMMIT=0a34fbda83724cc06266aadcf7c88acc8fef4e7d
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) gnu=x86_64-linux-gnu; arch=x86_64 ;; \
      arm64) gnu=aarch64-linux-gnu; arch=aarch64 ;; \
      *) echo "unsupported architecture $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    if [ "$(dpkg --print-architecture)" = "$TARGETARCH" ]; then cc="gcc g++"; else cc="gcc-$(echo $gnu | tr _ -) g++-$(echo $gnu | tr _ -)"; fi; \
    dpkg --add-architecture "$TARGETARCH"; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git make autoconf automake libtool pkgconf binutils-$(echo $gnu | tr _ -) $cc \
      libc6-dev:$TARGETARCH libssl-dev:$TARGETARCH libpopt-dev:$TARGETARCH libconfig-dev:$TARGETARCH; \
    rm -rf /var/lib/apt/lists/*; \
    fetch() { mkdir -p "$1"; cd "$1"; git init -q; git fetch -q --depth 1 "$2" "$3"; git checkout -q FETCH_HEAD; }; \
    fetch /src/ffmpeg https://github.com/FFmpeg/FFmpeg.git "$FFMPEG_COMMIT"; \
    ./configure --prefix=/opt/ffmpeg --enable-cross-compile --cross-prefix="$gnu-" --arch="$arch" --target-os=linux \
      --disable-everything --disable-programs --disable-doc --disable-network --disable-autodetect --disable-x86asm \
      --enable-static --disable-shared --enable-pic --enable-decoder=alac,aac,pcm_s16be --enable-swresample; \
    make -j"$(nproc)"; make install; \
    fetch /src/shairport-sync https://github.com/mikebrady/shairport-sync.git "$SHAIRPORT_COMMIT"; \
    autoreconf -fi; \
    PKG_CONFIG_LIBDIR="/opt/ffmpeg/lib/pkgconfig:/usr/lib/$gnu/pkgconfig:/usr/share/pkgconfig" \
      ./configure --host="$gnu" --sysconfdir=/etc --with-ssl=openssl --with-external-mdns --with-stdout --with-pipe \
      --with-metadata --with-metadata-pipe --without-configfiles --without-create-user-group; \
    make -j"$(nproc)"; \
    mkdir -p /out; cp shairport-sync /out/; "$gnu-strip" /out/shairport-sync; \
    rm -rf /src /opt/ffmpeg

# ffmpeg for the equaliser and balance (server/rioserver/eq.py): MP3 in, the
# filters eq.filters() uses, MP3 out through libmp3lame, and nothing else.
# Debian's ffmpeg package would add about 160 MB to the image. libmp3lame is
# linked dynamically: the final image has it through the lame package.
FROM --platform=$BUILDPLATFORM debian:trixie-slim AS ffmpeg
ARG TARGETARCH
ARG FFMPEG_COMMIT
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) gnu=x86_64-linux-gnu; arch=x86_64 ;; \
      arm64) gnu=aarch64-linux-gnu; arch=aarch64 ;; \
      *) echo "unsupported architecture $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    if [ "$(dpkg --print-architecture)" = "$TARGETARCH" ]; then cc="gcc"; else cc="gcc-$(echo $gnu | tr _ -)"; fi; \
    dpkg --add-architecture "$TARGETARCH"; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git make pkgconf binutils-$(echo $gnu | tr _ -) $cc \
      libc6-dev:$TARGETARCH libmp3lame-dev:$TARGETARCH; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir /src; cd /src; git init -q; \
    git fetch -q --depth 1 https://github.com/FFmpeg/FFmpeg.git "$FFMPEG_COMMIT"; \
    git checkout -q FETCH_HEAD; \
    ./configure --enable-cross-compile --cross-prefix="$gnu-" --arch="$arch" --target-os=linux \
      --disable-everything --disable-doc --disable-network --disable-autodetect --disable-x86asm \
      --disable-ffprobe --disable-ffplay --enable-ffmpeg --enable-static --disable-shared \
      --enable-libmp3lame --enable-swresample \
      --enable-protocol=file,pipe --enable-demuxer=mp3 --enable-parser=mpegaudio \
      --enable-decoder=mp3,mp3float --enable-encoder=libmp3lame --enable-muxer=mp3 \
      --enable-filter=abuffer,abuffersink,aformat,aresample,anull,volume,equalizer,pan,alimiter; \
    make -j"$(nproc)"; \
    mkdir -p /out; cp ffmpeg /out/; "$gnu-strip" /out/ffmpeg; \
    rm -rf /src

FROM python:3.12-slim-trixie
LABEL org.opencontainers.image.title="RioServer NG" \
      org.opencontainers.image.description="Music and network-boot server for the Rio Receiver and Dell Digital Audio Receiver"
ENV PYTHONUNBUFFERED=1 \
    RIO_MUSIC_DIR=/music \
    RIO_DATA_DIR=/data \
    RIO_FIRMWARE_DIR=/firmware
# lame encodes live audio (AirPlay, Spotify) to MP3 for the receivers, and
# its libmp3lame is also the equaliser's ffmpeg's; libconfig and libpopt are
# shairport-sync's.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends lame libconfig11 libpopt0 \
 && rm -rf /var/lib/apt/lists/*
COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY --from=librespot /out/librespot /usr/local/bin/
COPY --from=shairport /out/shairport-sync /usr/local/bin/
COPY --from=ffmpeg /out/ffmpeg /usr/local/bin/
COPY server/rioserver /app/rioserver
COPY --from=firmware /out/ /firmware/
WORKDIR /app
VOLUME ["/data"]
# 12078/tcp  Rio music protocol (the port the original server used)
# 8080/tcp   web interface (also served on 12078)
# 21075/udp  receiver discovery
# 111, 627, 2049 tcp+udp  portmapper, mountd and NFS for network boot
EXPOSE 12078/tcp 8080/tcp 21075/udp 111/udp 111/tcp 627/udp 627/tcp 2049/udp 2049/tcp
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('RIO_HTTP_PORT', '12078'), timeout=4)"
CMD ["python", "-m", "rioserver"]
