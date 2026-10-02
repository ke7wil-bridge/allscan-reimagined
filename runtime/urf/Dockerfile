FROM debian:bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git patch ca-certificates libcurl4-gnutls-dev nlohmann-json3-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone https://github.com/nostar/urfd.git && cd urfd && git checkout bd0c114e43adc3df4c17823bc5693328425d0450
RUN git clone https://github.com/nostar/tcd.git && cd tcd && git checkout ff104384b3d1c51bf8f7f9f83d7940dffda0b076
RUN git clone https://github.com/nostar/imbe_vocoder.git && \
    cd imbe_vocoder && git checkout 03423cb00b8e9ba1be164493e8b98b5bd7c1508c && make -j"$(nproc)" && make install && ldconfig
COPY patches/tcd/Controller.cpp /src/tcd/Controller.cpp
COPY patches/Reflector.cpp /src/urfd/reflector/Reflector.cpp
COPY patches/Clients.cpp /src/urfd/reflector/Clients.cpp
COPY patches/Clients.h /src/urfd/reflector/Clients.h
COPY patches/CodecStream.cpp /src/urfd/reflector/CodecStream.cpp
COPY patches/DMRMMDVMProtocol.cpp /src/urfd/reflector/DMRMMDVMProtocol.cpp
COPY patches/M17Protocol.cpp /src/urfd/reflector/M17Protocol.cpp
COPY patches/M17Client.h /src/urfd/reflector/M17Client.h
COPY patches/M17Client.cpp /src/urfd/reflector/M17Client.cpp
COPY patches/USRPProtocol.cpp /src/urfd/reflector/USRPProtocol.cpp
COPY patches/M17Packet.cpp /src/urfd/reflector/M17Packet.cpp
COPY patches/DVHeaderPacket.cpp /src/urfd/reflector/DVHeaderPacket.cpp
COPY patches/YSFProtocol-session-suppression.patch /tmp/YSFProtocol-session-suppression.patch
RUN patch -d /src/urfd -p1 < /tmp/YSFProtocol-session-suppression.patch
COPY patches/P25Protocol-session-suppression.patch /tmp/P25Protocol-session-suppression.patch
RUN patch -d /src/urfd -p1 < /tmp/P25Protocol-session-suppression.patch
COPY patches/NXDNProtocol-session-suppression.patch /tmp/NXDNProtocol-session-suppression.patch
RUN patch -d /src/urfd -p1 < /tmp/NXDNProtocol-session-suppression.patch
RUN cp urfd/config/urfd.mk urfd/reflector/urfd.mk && \
    sed -i 's/^DHT = true/DHT = false/' urfd/reflector/urfd.mk && \
    make -C urfd/reflector -j"$(nproc)"
RUN cp tcd/config/tcd.mk tcd/tcd.mk && \
    sed -i 's/^swmodes = false/swmodes = true/' tcd/tcd.mk && \
    make -C tcd -j"$(nproc)"

FROM debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libcurl4 libcurl3-gnutls libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/lib/libimbe_vocoder* /usr/local/lib/
COPY --from=build /src/urfd/reflector/urfd /usr/local/bin/urfd
COPY --from=build /src/urfd/reflector/inicheck /usr/local/bin/inicheck
COPY --from=build /src/urfd/reflector/dbutil /usr/local/bin/dbutil
COPY --from=build /src/tcd/tcd /usr/local/bin/tcd
RUN ldconfig
ENTRYPOINT ["/bin/sh","-c"]
