# Terminal-backend image for the TP/integrations assistant.
#
# Extends the default Hermes docker terminal image with archive-extraction and
# fast-search tooling so the agent can unpack zip/7z/rar archives (delivered via
# chat attachments or downloaded from the internet into /workspace) and grep
# them. `unar` (The Unarchiver) is free (Debian main) and handles rar — incl.
# RAR5 — plus 7z and zip in one package, so no non-free repo is needed.
#
# Build:  docker build -t hermes-tp:latest deploy/tp-assistant
# Wire:   terminal.docker_image: "hermes-tp:latest"  in config.yaml
FROM nikolaik/python-nodejs:python3.11-nodejs20

# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends unar ripgrep \
 && rm -rf /var/lib/apt/lists/*
