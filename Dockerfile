# RioServer NG: music server and network-boot server for the Rio Receiver /
# Dell Digital Audio Receiver.
#
#   docker build -t rioserver-ng .
#
# Stage 1 cross-compiles the receiver firmware; stage 2 is the server.

# Ubuntu 24.04 ships arm-none-eabi-gcc 13.2, the compiler the firmware is tested with.
# The firmware runs on the receiver, not the server, so this stage always runs
# natively on the build machine, even when building an arm64 server image.
FROM --platform=$BUILDPLATFORM ubuntu:24.04 AS firmware
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gcc-arm-none-eabi make python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src/firmware
COPY firmware/ ./
# Fetches libmad and the RioPlay kernel image from SourceForge (checksummed).
RUN make clean && make \
 && mkdir -p /out \
 && cp build/receiver-ng.arf build/deps/receiver-rioplay.arf /out/

FROM python:3.12-slim
LABEL org.opencontainers.image.title="RioServer NG" \
      org.opencontainers.image.description="Music and network-boot server for the Rio Receiver and Dell Digital Audio Receiver"
ENV PYTHONUNBUFFERED=1 \
    RIO_MUSIC_DIR=/music \
    RIO_DATA_DIR=/data \
    RIO_FIRMWARE_DIR=/firmware
COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
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
