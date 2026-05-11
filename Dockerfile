# Use miniconda as base image
FROM continuumio/miniconda3:latest

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy conda environment file
COPY environment.yml .

# Create conda environment
RUN conda env create -f environment.yml

# Make RUN commands use the new environment
SHELL ["conda", "run", "-n", "freqtrade-data-service", "/bin/bash", "-c"]

# Upgrade websockets to 13+ for native proxy support (BINANCE_PROXY_URL).
# Done as a separate layer so the heavy conda env layer stays cached.
RUN pip install --no-cache-dir 'websockets>=13,<15' 'python-socks[asyncio]>=2.4'

# Copy application code
COPY src/ ./src/
COPY config/ ./config/

# Create necessary directories
RUN mkdir -p /app/logs /app/data

# Set Python path
ENV PYTHONPATH=/app:$PYTHONPATH
ENV PYTHONUNBUFFERED=1

# Expose ports
EXPOSE 8000 8001

# Health check (using conda run)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Run the application with conda
CMD ["conda", "run", "--no-capture-output", "-n", "freqtrade-data-service", "python", "-m", "src.main"]