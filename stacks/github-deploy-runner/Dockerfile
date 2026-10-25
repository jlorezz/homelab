FROM ghcr.io/actions/actions-runner@sha256:660f7b9d1e0007274f7c867e220b3c382137ef5a30d8e501a025ad50dbd5fb9d
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential python3 postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mv /home/runner /opt/actions-runner \
    && mkdir -p /runner /home/ci /work /opt/hostedtoolcache \
    && chown -R 1001:123 /runner /home/ci /work /opt/hostedtoolcache
# Chromium system libraries for the release job's browser checks (Dealpipe
# #774/#795). Same package set as the live image sha256:852de79d..., which was
# layered on by hand on 2026-10-21; the job downloads its pinned Chromium itself.
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb fonts-noto-color-emoji fonts-unifont libfontconfig1 libfreetype6 \
    xfonts-cyrillic xfonts-scalable fonts-liberation fonts-ipafont-gothic \
    fonts-wqy-zenhei fonts-tlwg-loma-otf fonts-freefont-ttf libasound2t64 \
    libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libcairo2 libcups2t64 \
    libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 libnspr4 libnss3 libpango-1.0-0 \
    libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    libxkbcommon0 libxrandr2 \
    && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/deploy-runner
COPY job-hook.py /usr/local/lib/agent-platform/job-hook.py
COPY custody.py /usr/local/lib/agent-platform/custody.py
COPY workload.py /usr/local/lib/agent-platform/workload.py
RUN printf '#!/bin/bash\nexec python3 /usr/local/lib/agent-platform/job-hook.py started\n' > /usr/local/bin/ci-job-started.sh \
    && printf '#!/bin/bash\nexec python3 /usr/local/lib/agent-platform/job-hook.py completed\n' > /usr/local/bin/ci-job-completed.sh \
    && chmod 755 /usr/local/bin/deploy-runner /usr/local/bin/ci-job-started.sh /usr/local/bin/ci-job-completed.sh
ENV HOME=/home/ci RUNNER_TOOL_CACHE=/opt/hostedtoolcache \
    NPM_CONFIG_PREFIX=/home/ci/.local PATH=/home/ci/.local/bin:$PATH \
    AGENT_CI_EXCLUSIVE=1 \
    ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/ci-job-started.sh \
    ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/ci-job-completed.sh
USER 1001:123
WORKDIR /runner
ENTRYPOINT ["/usr/local/bin/deploy-runner"]
