# alpine-srv6-scapy:1.0
#
# Lab host image. Provides:
#   - kernel SRv6 utilities (from upstream alpine-srv6 base)
#   - Python 3 + scapy (for spray.py userspace SRv6/uSID packet gen)
#   - the srv6_fabric Python package, pip-installed so its console
#     scripts (`spray`, `routes`, `run-scenario`) land in /usr/local/bin
#   - a baked-in topo.yaml at /etc/srv6_fabric/topo.yaml plus
#     SRV6_TOPO env var so the package's runtime constants
#     (NUM_PLANES etc.) match the topology this image was built for.
#
# Build (from repo root — context MUST be repo root, not host-image/):
#
#   docker build -f host-image/Dockerfile \
#                --build-arg TOPO=topologies/4p-8x16/topo.yaml \
#                -t alpine-srv6-scapy:1.0 .
#
# Used by every green/yellow host node in
# topologies/<name>/topology.clab.yaml. No bind mounts are needed for
# normal operation; rebuild the image when the package or topo.yaml
# changes.

FROM iejalapeno/alpine-srv6:1.0

ARG TOPO=topologies/4p-8x16/topo.yaml

RUN apk add --no-cache \
        python3 \
        py3-pip \
        tcpdump

# Install srv6_fabric and its runtime deps (scapy, PyYAML).
# Copy the minimal set: pyproject + package source + a README stub
# (referenced by [project].readme in pyproject.toml).
WORKDIR /tmp/srv6_fabric_build
COPY pyproject.toml README.md ./
COPY srv6_fabric ./srv6_fabric

RUN pip3 install --no-cache-dir --break-system-packages \
        '.[runtime]' \
 && cd / && rm -rf /tmp/srv6_fabric_build

# Bake the topology descriptor at a stable in-image path, and
# point the package at it. The build-arg TOPO is repo-relative.
RUN mkdir -p /etc/srv6_fabric
COPY ${TOPO} /etc/srv6_fabric/topo.yaml
ENV SRV6_TOPO=/etc/srv6_fabric/topo.yaml

# Sanity: surface versions and confirm the package loaded the topology.
RUN python3 -c "import scapy; print('scapy', scapy.__version__)" \
 && python3 -c "import srv6_fabric.topo as t; print('srv6_fabric topo:', t._TOPO.get('name'), t.NUM_PLANES, 'planes x', t.NUM_SPINES, 'spines x', t.NUM_LEAVES, 'leaves')" \
 && which spray routes run-scenario
