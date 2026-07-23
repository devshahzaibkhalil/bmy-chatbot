# BMY Marketer AI Assistant - container image
# Runs the app through waitress (see wsgi.py). No external AI API used.

FROM python:3.12-slim

WORKDIR /app

# System deps: build tools for python-Levenshtein, libsndfile for SpeechRecognition's
# optional audio handling. Comment out python-Levenshtein in requirements.txt if you'd
# rather skip the build tools - fuzzywuzzy works without it, just a bit slower.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persisted at runtime - mount these as volumes so data survives container restarts
RUN mkdir -p database database/.keys uploads/documents backups

EXPOSE 5000

# Create the first admin account once, interactively, after the container is up:
#   docker exec -it <container> python setup_admin.py
CMD ["python", "wsgi.py"]
