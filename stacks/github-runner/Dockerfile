FROM ghcr.io/actions/actions-runner@sha256:660f7b9d1e0007274f7c867e220b3c382137ef5a30d8e501a025ad50dbd5fb9d
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential python3 postgresql-client zip zstd ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mv /home/runner /opt/actions-runner \
    && mkdir -p /runner /home/ci /var/lib/dealpipe-ci/work /opt/hostedtoolcache
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL https://github.com/docker/compose/releases/download/v5.6.0/docker-compose-linux-x86_64 \
        -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && echo '40343e21ca777173e69cff5dbafeb37c6f81f3b0d57d9e597f036e95eb63e76a  /usr/local/lib/docker/cli-plugins/docker-compose' | sha256sum -c - \
    && chmod 755 /usr/local/lib/docker/cli-plugins/docker-compose \
    && docker compose version
# Current Dealpipe development packageManager; update this pin when rebuilding for a bump.
RUN test "$(uname -m)" = x86_64 \
    && curl -fsSL https://github.com/oven-sh/bun/releases/download/bun-v1.3.11/bun-linux-x64.zip -o /tmp/bun.zip \
    && echo '8611ba935af886f05a6f38740a15160326c15e5d5d07adef966130b4493607ed  /tmp/bun.zip' | sha256sum -c - \
    && mkdir -p /opt/ci-runtime \
    && python3 -c 'import hashlib, pathlib, zipfile; data = zipfile.ZipFile("/tmp/bun.zip").read("bun-linux-x64/bun"); pathlib.Path("/opt/ci-runtime/bun").write_bytes(data); pathlib.Path("/opt/ci-runtime/bun.sha256").write_text(hashlib.sha256(data).hexdigest() + "\n")' \
    && chmod 755 /opt/ci-runtime/bun \
    && cd /opt/ci-runtime && echo "$(cat bun.sha256)  bun" | sha256sum -c - \
    && test "$(./bun --version)" = 1.3.11 \
    && rm /tmp/bun.zip
COPY runtime_bootstrap.py /usr/local/lib/agent-platform/runtime_bootstrap.py
COPY entrypoint.sh /usr/local/bin/ci-runner
COPY workload.py /usr/local/lib/agent-platform/workload.py
COPY worker-custody.c /tmp/worker-custody.c
COPY custody.py /usr/local/lib/agent-platform/custody.py
RUN gcc -shared -fPIC -pthread -O2 -Wall -Wextra -Werror -o /usr/local/lib/agent-platform/worker-custody.so /tmp/worker-custody.c \
    && rm /tmp/worker-custody.c
# Applies across runner auto-updates; constructor is inert for all other executables.
ENV LD_PRELOAD=/usr/local/lib/agent-platform/worker-custody.so
COPY job-hook.py /usr/local/lib/agent-platform/job-hook.py
RUN printf '#!/bin/bash\nexec python3 /usr/local/lib/agent-platform/job-hook.py started\n' > /usr/local/bin/ci-job-started.sh \
    && printf '#!/bin/bash\nexec python3 /usr/local/lib/agent-platform/job-hook.py completed\n' > /usr/local/bin/ci-job-completed.sh \
    && chmod 755 /usr/local/bin/ci-job-started.sh /usr/local/bin/ci-job-completed.sh
RUN chmod 755 /usr/local/bin/ci-runner
ENV HOME=/home/ci RUNNER_ALLOW_RUNASROOT=1 RUNNER_TOOL_CACHE=/opt/hostedtoolcache
ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/ci-job-started.sh
ENV ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/ci-job-completed.sh
WORKDIR /runner
ENTRYPOINT ["/usr/local/bin/ci-runner"]
