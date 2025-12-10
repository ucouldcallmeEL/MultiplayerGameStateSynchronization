#!/bin/bash

# =================================================================
#  Grid Clash - Automated Test Runner (Phase 1 & Phase 2)
# =================================================================

# 1. Check Root Privileges
if [ "$EUID" -ne 0 ]; then
  echo "❌ Error: This script requires root privileges to control network traffic."
  echo "👉 Usage: sudo ./run_all_tests.sh"
  exit 1
fi

echo "========================================================="
echo " Setting up Environment..."
echo "========================================================="

# 2. Check/Install Python Dependencies
# We use 'psutil' for CPU logging. Only install if missing.
if ! python3 -c "import psutil" &> /dev/null; then
    echo "📦 Installing Python 'psutil'..."
    apt-get update -qq
    apt-get install -y python3-pip
    pip3 install psutil
fi

# 3. Check System Tools
# We need 'tc' (iproute2) and 'tcpdump'
if ! command -v tc &> /dev/null || ! command -v tcpdump &> /dev/null; then
    echo "📦 Installing system network tools..."
    apt-get update -qq
    apt-get install -y iproute2 tcpdump python3-tk
fi

echo "✅ Environment Ready."
echo "========================================================="
echo " Starting Python Experiment Runner..."
echo "========================================================="

# 4. Make Python Scripts Executable
chmod +x run_experiments.py automated_client.py

# 5. Run the Master Script
# Using -u to unbuffer stdout so you see real-time progress
python3 -u run_experiments.py

echo "========================================================="
echo " 🎉 All Scenarios Completed."
echo " 📂 Results saved in 'test_results/' directory."
echo "========================================================="