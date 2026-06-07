#!/bin/bash
set -e

echo "Installing Maven..."
apt-get update -qq && apt-get install -y -qq maven

echo "Installing Python packages..."
pip install -r requirements.txt

echo "Build complete!"
