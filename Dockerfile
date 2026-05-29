FROM python:3.12-slim

WORKDIR /app

# Install system dependencies if needed (e.g., for sqlite or network tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure the app can write to the database
RUN touch drafts.db

EXPOSE 3010

CMD ["uvicorn", "ui:web_app", "--host", "0.0.0.0", "--port", "3010"]
