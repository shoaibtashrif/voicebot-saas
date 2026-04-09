#!/bin/bash

# Agent Management Service Startup Script

echo "🚀 Starting Agent Management Service..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

# Set environment variables
export PORT=5005
export HOST=0.0.0.0

# Start the service
echo "🌟 Starting service on port $PORT..."
python main.py
