FROM python:3.11-slim

# 1) Install OS-level build dependencies (gcc, headers, etc.)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      libssl-dev \
      libffi-dev \
      curl \
      ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Copy only requirements, upgrade pip & friends, then install
COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -r requirements.txt

# 3) Copy your bot code
COPY . .

EXPOSE 8080
CMD ["python", "main.py"]
