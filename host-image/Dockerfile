# alpine-srv6-scapy:1.0
#
# Adds Python 3 + scapy to the upstream alpine-srv6 image so hosts can run
# tools/spray.py (userspace SRv6/uSID packet generator).
#
# Build:
#   docker build -t alpine-srv6-scapy:1.0 tools/
#
# Used by every green/yellow host in topology.clab.yaml. The host's
# /tools directory is bind-mounted from the lab's tools/ at clab deploy time
# so spray.py edits are immediately visible inside the containers (no rebuild
# needed for script changes — only image-level deps require a rebuild).

FROM iejalapeno/alpine-srv6:1.0

RUN apk add --no-cache \
        python3 \
        py3-pip \
        tcpdump \
    && pip3 install --no-cache-dir --break-system-packages \
        scapy

# Sanity: surface the version at build time.
RUN python3 -c "import scapy; print('scapy', scapy.__version__)"
