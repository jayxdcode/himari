#!/bin/bash

# Step 1: Install ffmpeg
echo "[*] Installing ffmpeg..."
sudo apt update && sudo apt install -y ffmpeg python3

# Step 2: Locate the ffmpeg binary
echo "[*] Locating ffmpeg binary..."
FFMPEG_PATH=$(which ffmpeg)
echo "Located ffmpeg at: $FFMPEG_PATH"

#Step 3: Install requirements
echo "[*] Installing requirements..."
sudo pip install -r requirements.txt

# Run
python3 main.py