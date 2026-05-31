#!/bin/bash
# Exit the script instantly if any individual command fails
set -e

echo "🚀 Booting Full-Stack Automated Test Matrix..."

echo "------------------------------------------------"
echo "Phase 1: Validating Backend Server Routing Architecture"
node test_suite.js

echo "------------------------------------------------"
echo "Phase 2: Validating Edge Layer AI Inference Thresholds"
# Step out of backend/test to the root directory, then enter edge_device
cd ../../edge_device
python test_suite.py

echo "------------------------------------------------"
echo "🏆 All automated verification profiles completed cleanly!"