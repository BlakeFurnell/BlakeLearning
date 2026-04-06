FROM python:3.11-slim

# System deps:
#   libmagic1      — file-type detection (used by python-magic if added later)
#   build-essential — compiles any C extensions in the dependency tree
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmagic1 \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps before copying app code so this layer is cached as long
# as requirements.txt is unchanged — keeps rebuilds fast.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application.
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
