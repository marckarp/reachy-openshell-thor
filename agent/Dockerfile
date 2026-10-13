# Sandbox image for the single-file agent. OpenShell runs this as the "reachy-agent" sandbox.
# Only Python, the OpenAI client (to talk to build.nvidia.com) and httpx (to talk to the robot).
FROM python:3.12-slim-bookworm

# OpenShell needs `ip` and nftables inside the sandbox to build its isolated network.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 nftables \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir "openai>=2.1" "httpx>=0.27"

# Unprivileged user the policy's process section expects.
RUN groupadd --gid 1000660000 sandbox && useradd --no-log-init --uid 1000660000 --gid 1000660000 --create-home sandbox \
    && mkdir -p /sandbox && chown sandbox:sandbox /sandbox

COPY minimal_agent.py /sandbox/minimal_agent.py
WORKDIR /sandbox
USER sandbox

# Stay alive; talk.sh runs the agent with `openshell sandbox exec`.
CMD ["/bin/sleep", "infinity"]
