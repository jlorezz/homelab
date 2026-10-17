FROM docker.io/nousresearch/hermes-agent@sha256:a11574b7fe8e1631d7d88d556677dac693bc058abf558cdeb899155688c1bf05

# Hermes 0.22.2's detached dashboard actions choose a bare PM interpreter,
# although this image ships its dependencies in .venv. Keep those actions
# inside the bundled environment, as the container's main launcher does.
COPY patch-launcher.py /tmp/patch-hermes-launcher.py
RUN /opt/hermes/.venv/bin/python /tmp/patch-hermes-launcher.py \
    && rm /tmp/patch-hermes-launcher.py
