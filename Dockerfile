FROM python:3.11-slim

# Create app directory
WORKDIR /app

# Install build deps and install requirements
COPY requirements.txt ./
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Copy bot script
COPY scripts/ ./scripts/

# Use a non-root user
RUN useradd --create-home botuser || true
USER botuser

ENV PYTHONUNBUFFERED=1

CMD ["python", "scripts/telegram_bot.py"]
