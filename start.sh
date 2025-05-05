#!/bin/bash

# Step 1: Install ffmpeg
echo "[*] Installing ffmpeg..."
sudo apt update && sudo apt install -y ffmpeg

# Step 2: Locate the ffmpeg binary
echo "[*] Locating ffmpeg binary..."
FFMPEG_PATH=$(which ffmpeg)
echo "Located ffmpeg at: $FFMPEG_PATH"

# Step 3: Clone the repo if not present
REPO_URL="https://github.com/jayxdcode/himari.git"
REPO_NAME="himari"

if [ ! -d "$REPO_NAME" ]; then
    echo "[*] Cloning the repo..."
    git clone "$REPO_URL"
fi

cd "$REPO_NAME" || exit 1

# Step 4: Copy ffmpeg binary into the repo
echo "[*] Copying ffmpeg binary into the repo..."
cp "$FFMPEG_PATH" ./ffmpeg

# Run
python3 main.py
