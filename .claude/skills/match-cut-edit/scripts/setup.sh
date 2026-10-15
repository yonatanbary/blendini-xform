#!/usr/bin/env bash
# Installs everything matchcut.py needs (idempotent).
set -e
command -v ffmpeg >/dev/null || { apt-get update -qq && apt-get install -y -qq ffmpeg; }
ldconfig -p | grep -q libEGL.so.1 || { apt-get update -qq; apt-get install -y -qq libegl1 libgles2; }   # MediaPipe needs EGL even on CPU
python3 -c "import cv2, mediapipe" 2>/dev/null || pip install -q opencv-python-headless mediapipe numpy
echo "matchcut deps ok"
