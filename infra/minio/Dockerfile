FROM golang:1.24.8-alpine AS build

RUN apk add --no-cache ca-certificates git
WORKDIR /src
RUN git init \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth=1 origin 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a \
    && git checkout --detach FETCH_HEAD
RUN CGO_ENABLED=0 go build -trimpath -o /out/minio .

FROM alpine:3.22

RUN apk add --no-cache ca-certificates \
    && addgroup -S -g 1000 minio \
    && adduser -S -D -H -u 1000 -G minio minio \
    && mkdir -p /data \
    && chown minio:minio /data
COPY --from=build /out/minio /usr/bin/minio
USER minio
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]

