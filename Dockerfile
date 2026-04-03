FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (to leverage Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the core application files
COPY src/ src/
COPY main.py .

# Best practice: define a volume for any local database files if they are generated here
VOLUME ["/app/data"]

# Run the main application
CMD ["python", "main.py"]
