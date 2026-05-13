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
SHELL ["conda", "run", "-n", "candleforge", "/bin/bash", "-c"]

# Upgrade websockets to 14+ (proxy= kwarg) + python-socks[asyncio] (SOCKS5 backend).
# Without python-socks the proxy kwarg falls through to asyncio.create_connection
# which doesn't accept it. Separate layer keeps the heavy conda env layer cached.
RUN pip install --no-cache-dir 'websockets>=14,<16' 'python-socks[asyncio]>=2.4'

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
CMD ["conda", "run", "--no-capture-output", "-n", "candleforge", "python", "-m", "src.main"]