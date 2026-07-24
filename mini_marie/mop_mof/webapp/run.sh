#!/bin/bash

# MOP Extraction Agent - Web Extension Startup Script

echo "========================================================================"
echo "MOP Extraction Agent - Web Extension"
echo "Web Application Startup"
echo "========================================================================"
echo ""

# Check if we're in the right directory
if [ ! -f "app.py" ]; then
    echo "❌ Error: app.py not found!"
    echo "Please run this script from the mini_marie/webapp directory"
    exit 1
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️  Warning: .env file not found"
    echo "Please create a .env file with your API key:"
    echo "  cp env.example .env"
    echo "  # Then edit .env and add your OPENAI_API_KEY"
    echo ""
    read -p "Do you want to continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✅ Found .env file"
fi

# Check Python version
python_version=$(python --version 2>&1 | awk '{print $2}')
echo "🐍 Python version: $python_version"

# Check if dependencies are installed
echo ""
echo "📦 Checking dependencies..."
if python -c "import flask" 2>/dev/null; then
    echo "✅ Flask installed"
else
    echo "❌ Flask not found. Installing dependencies..."
    pip install -r requirements.txt
fi

# Navigate to project root for proper imports
cd ../..
export PYTHONPATH=$PWD

echo ""
echo "🚀 Starting MOP Extraction Agent web extension..."
echo "📍 URL: http://127.0.0.1:5000"
echo "🛑 Press Ctrl+C to stop"
echo ""
echo "========================================================================"
echo ""

# Run the app
python mini_marie/webapp/app.py --host 127.0.0.1 --port 5000 ${1:---debug}

