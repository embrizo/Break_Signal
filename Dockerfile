# Multi-arch (builds natively on the Pi 5's arm64).
FROM python:3.11-slim-bookworm

# System libs matplotlib/mplfinance need at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfreetype6 libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Leverage the piwheels index on ARM so numpy/pandas/matplotlib ship as
# prebuilt wheels instead of compiling from source (minutes vs an hour).
COPY requirements.txt .
RUN pip install --no-cache-dir \
        --extra-index-url https://www.piwheels.org/simple \
        -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    BREAK_SIGNAL_CONFIG=/app/config.yaml \
    MPLBACKEND=Agg

# config.yaml and the state db are mounted in via compose, not baked in.
CMD ["python", "-m", "break_signal"]
