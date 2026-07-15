# Overlay image for the multi-agent deploy.
#
# Adds the runtime deps our custom plugins need, on top of the UPSTREAM base
# image (built from the repo-root Dockerfile). This file never edits the
# upstream Dockerfile, so `git pull origin` stays conflict-free.
#
# Build the base once, then this overlay:
#   docker build -t hermes-agent:base .                       # from repo root (upstream Dockerfile)
#   docker build -t hermes-multiagent:latest deploy/multi-agent
#
# Deps added:
#   - gh CLI                    -> github plugin (shells out to `gh`)
#   - lxml                      -> curl-jina web_extract (Tier-1 HTML parse)
#   - google-api-python-client  -> google-docs / google-sheets
#   - google-auth[-oauthlib]    -> google-* auth (token / service account)
ARG BASE_IMAGE=hermes-agent:base
FROM ${BASE_IMAGE}

USER root

# gh CLI from GitHub's apt repo (not in Debian main). curl + ca-certificates
# already present in the base image.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      gh unar p7zip-full sshpass \
      avahi-daemon avahi-utils libnss-mdns dbus \
 && rm -rf /var/lib/apt/lists/*
# gh                       -> github plugin
# unar, p7zip              -> archive plugin / code_execution: unar covers zip/rar/RAR5/7z
# sshpass                  -> ssh plugin: non-interactive login/password auth
# (openssh-client — ssh/ssh-keygen/scp — is already in the base image)
# avahi-daemon + libnss-mdns -> TRANSPARENT mDNS: `.local` names resolve natively
#   (getent/ssh/curl) via NSS, and avahi-browse discovers WB controllers. The
#   daemon runs with publishing DISABLED (see avahi-daemon.conf) so it coexists
#   with a host avahi on 5353 without a name-conflict. Needs network_mode: host.
# avahi-utils              -> avahi-browse / avahi-resolve
# dbus                     -> avahi-daemon's system bus (avahi-browse talks over it)

# Python deps for the custom plugins, installed into the sealed base venv.
# uv + the venv are provided by the base image.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache-dir \
      lxml \
      google-api-python-client \
      "google-auth" \
      google-auth-oauthlib \
      ddgs \
      py7zr rarfile          # pure-Python archive libs for code_execution
# ddgs                       -> web_search backend (parent-side; must be in this env)
# hindsight-client           -> long-term memory (memory.provider: hindsight)
# py7zr/rarfile              -> 7z/rar from Python without shelling out

# Toolbox for code_execution — handy libs for programmers & support engineers.
# terminal is disabled, so Python is the agent's scratch shell; stock these so it
# can parse, compute, and poke WB protocols without shelling out.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache-dir \
      pandas numpy tabulate openpyxl \
      beautifulsoup4 html5lib jmespath pyyaml python-dateutil \
      crcmod pymodbus pyserial paho-mqtt \
      matplotlib rich humanize \
      schemdraw graphviz \
      skidl
# schemdraw                      -> электрические принципиалки кодом (УГО → PNG/SVG); поверх matplotlib
# skidl                          -> электрическая модель схемы + ERC (проверка корректности); wb-wiring рисует schemdraw'ом, валидирует skidl'ом
# graphviz (py)                  -> блок-схемы/флоучарты через DOT (бинарь `dot` уже в базовом образе)
# pandas/numpy/tabulate/openpyxl -> tabular data: logs, CSV/XLSX (e.g. книга жалоб), telemetry
# beautifulsoup4/html5lib        -> HTML parsing alongside web_extract (support portal, wiki)
# jmespath/pyyaml/python-dateutil-> query wb-cli --json output, read WB YAML configs, parse log timestamps
# crcmod/pymodbus/pyserial       -> RS-485/Modbus: CRC16, build/decode frames (WB serial debugging)
# paho-mqtt                      -> construct/analyze MQTT payloads (WB is MQTT-centric)
# matplotlib                     -> quick plots of telemetry/logs; rich/humanize -> readable output

# Bitwarden CLI for the bitwarden (vault) plugin. Node is present in the full
# base image; the standalone `bw` binary would also work.
RUN npm install -g @bitwarden/cli && npm cache clean --force

# mDNS: publish-disabled avahi config + s6 services (dbus + avahi-daemon, root).
# With publishing off, the container's avahi resolves/browses the LAN but never
# announces its own name, so it coexists with a host avahi on 5353 (no conflict).
# libnss-mdns (installed above, wires itself into nsswitch) then makes `.local`
# resolve transparently for every tool. Merged into the base s6-rc.d tree; the
# user/ contents.d markers add the services without touching main-hermes/dashboard.
COPY avahi-daemon.conf /etc/avahi/avahi-daemon.conf
COPY s6-rc.d/ /etc/s6-overlay/s6-rc.d/

# Entrypoint / CMD / user are inherited from the base image unchanged.
