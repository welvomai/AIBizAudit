#!/usr/bin/env bash
set -e

echo "Installing system dependencies for WeasyPrint..."
apt-get update -qq
apt-get install -y -qq \
  python3-cffi \
  python3-brotli \
  libpango-1.0-0 \
  libpangoft2-1.0-0 \
  libpangocairo-1.0-0 \
  libgdk-pixbuf2.0-0 \
  libffi-dev \
  shared-mime-info \
  fonts-liberation

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Build complete ✅"