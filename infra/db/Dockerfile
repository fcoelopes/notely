# PostgreSQL com TimescaleDB (base) + Apache AGE, para desenvolvimento local.
#
# A imagem timescale/timescaledb é Alpine (musl), então o AGE precisa ser
# compilado aqui mesmo; o .so pré-compilado do apache/age é glibc (Debian) e
# não roda no Alpine. A base já traz pgxs.mk e os headers do servidor.

FROM timescale/timescaledb:2.30.2-pg17

RUN apk add --no-cache build-base bison flex git perl \
    && git clone --depth 1 --branch PG17/v1.7.0-rc0 https://github.com/apache/age.git /tmp/age \
    && cd /tmp/age \
    && make PG_CONFIG=/usr/local/bin/pg_config with_llvm=no \
    && make install PG_CONFIG=/usr/local/bin/pg_config with_llvm=no \
    && cd / && rm -rf /tmp/age
