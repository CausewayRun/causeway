FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    sqlite3 \
    jq \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running tests (Claude Code requirement)
RUN useradd -m -s /bin/bash testuser \
    && echo "testuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Install uv globally
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/uv \
    && cp /root/.local/bin/uvx /usr/local/bin/uvx
ENV PATH="/usr/local/bin:$PATH"

# Install Node.js and Claude Code CLI (global)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code

# Copy Causeway from PR code with testuser ownership
COPY --chown=testuser:testuser . /causeway

# Create test project directory with proper ownership
RUN mkdir -p /test-project && chown -R testuser:testuser /test-project

# Copy E2E scripts
COPY --chown=testuser:testuser tests/e2e/ /e2e/
RUN chmod +x /e2e/*.sh /e2e/*.py

# Create logs directory
RUN mkdir -p /e2e/logs && chown -R testuser:testuser /e2e

# Switch to testuser for remaining setup
USER testuser

# Install Causeway dependencies as testuser with CPU-only PyTorch
WORKDIR /causeway
RUN uv pip install --system torch --index-url https://download.pytorch.org/whl/cpu
RUN uv sync

# Initialize git repo in test project
WORKDIR /test-project
RUN git init && git config user.email "test@e2e.com" && git config user.name "E2E Test"

# Environment setup
ENV CAUSEWAY_CWD=/test-project
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/testuser
ENV CAUSEWAY_ROOT=/causeway

ENTRYPOINT ["/e2e/run_e2e_tests.sh"]
