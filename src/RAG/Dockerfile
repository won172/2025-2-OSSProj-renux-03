FROM python:3.11-slim-bookworm

# Install system dependencies (OpenJDK for Konlpy, build tools)
RUN apt-get update && apt-get install -y \
    default-jdk-headless \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set JAVA_HOME for Konlpy
ENV JAVA_HOME=/usr/lib/jvm/default-java

WORKDIR /app

# Copy requirements first to leverage cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "api.rag_service:app", "--host", "0.0.0.0", "--port", "8000"]
