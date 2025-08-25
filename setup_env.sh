#!/bin/bash

# Freqtrade Data Service Environment Setup Script
# This script sets up the conda environment and starts the service

set -e  # Exit on error

CONDA_ENV_NAME="freqtrade-data-service"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Setting up Freqtrade Data Service environment..."

# Check if conda is installed
if ! command -v conda &> /dev/null; then
    echo "❌ Conda is not installed. Please install Miniconda or Anaconda first."
    echo "📥 Download from: https://docs.conda.io/en/latest/miniconda.html"
    exit 1
fi

# Navigate to project directory
cd "$PROJECT_DIR"

# Check if environment already exists
if conda info --envs | grep -q "$CONDA_ENV_NAME"; then
    echo "📦 Conda environment '$CONDA_ENV_NAME' already exists."
    read -p "Do you want to recreate it? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "🗑️ Removing existing environment..."
        conda env remove -n "$CONDA_ENV_NAME" -y
    else
        echo "📦 Using existing environment."
        SKIP_CREATE=true
    fi
fi

# Create conda environment
if [ "$SKIP_CREATE" != true ]; then
    echo "📦 Creating conda environment from environment.yml..."
    conda env create -f environment.yml
fi

echo "✅ Environment setup completed!"

# Activate environment and show next steps
echo ""
echo "🎯 Next steps:"
echo "1. Activate the environment:"
echo "   conda activate $CONDA_ENV_NAME"
echo ""
echo "2. Start ClickHouse database:"
echo "   docker-compose up -d"
echo ""
echo "3. Configure environment variables (optional):"
echo "   cp .env.example .env"
echo "   # Edit .env with your API keys"
echo ""
echo "4. Start the data service:"
echo "   python -m src.main"
echo ""
echo "5. Check the API documentation:"
echo "   http://localhost:8000/docs"
echo ""

# Ask if user wants to start the service now
echo "🔥 Quick Start Options:"
echo ""
read -p "Do you want to start ClickHouse now? (Y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    echo "🐳 Starting ClickHouse database..."
    docker-compose up -d
    
    echo "⏳ Waiting for ClickHouse to start..."
    sleep 10
    
    echo "✅ ClickHouse is running!"
    echo "📊 ClickHouse Web UI: http://localhost:8123/play"
fi

echo ""
read -p "Do you want to activate the conda environment now? (Y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    echo "🔧 To activate the environment, run:"
    echo "conda activate $CONDA_ENV_NAME"
    
    # Try to activate in current shell (may not work in all cases)
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate "$CONDA_ENV_NAME" 2>/dev/null || true
    
    echo ""
    echo "🚀 You can now start the service with:"
    echo "python -m src.main"
fi

echo ""
echo "🎉 Setup completed successfully!"
echo "📚 Check PROJECT_PLAN.md for detailed development roadmap"
echo "🐛 Issues? Check logs/ directory or create an issue on GitHub"