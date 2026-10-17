FROM ghcr.io/actions/actions-runner@sha256:660f7b9d1e0007274f7c867e220b3c382137ef5a30d8e501a025ad50dbd5fb9d
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential python3 postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mv /home/runner /opt/actions-runner \
    && mkdir -p /runner /home/ci /work /opt/hostedtoolcache \
    && chown -R 1001:123 /runner /home/ci /work /opt/hostedtoolcache
COPY entrypoint.sh /usr/local/bin/deploy-runner
COPY job-hook.py /usr/local/lib/agent-platform/job-hook.py
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
