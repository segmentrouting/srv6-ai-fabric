FROM alpine:3.19

# Install curl and libc compatibility for Telegraf
RUN apk add --no-cache curl libc6-compat

# Download the full-featured Telegraf binary (includes gnmi plugin)
RUN curl -L -o /usr/bin/telegraf https://dl.influxdata.com/telegraf/releases/telegraf-1.34.2_linux_amd64.tar.gz && \
    mkdir -p /tmp/telegraf && \
    tar -xzf /usr/bin/telegraf -C /tmp/telegraf --strip-components=2 telegraf-1.34.2/usr/bin/telegraf && \
    mv /tmp/telegraf /usr/bin/telegraf && chmod +x /usr/bin/telegraf

# Minimal config to avoid error on boot; override with volume mount
RUN mkdir -p /etc/telegraf && echo "[agent]\ninterval = \"10s\"\nround_interval = true\n" > /etc/telegraf/telegraf.conf

ENTRYPOINT ["/usr/bin/telegraf"]
