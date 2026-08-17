FROM python:3.12-slim

LABEL maintainer="Revue <hello@revue.sh>"
LABEL name="revue-pipe"
LABEL description="Revue AI Code Review — Bitbucket Pipe"
LABEL vendor="Revue"
LABEL version="1.0.0"

WORKDIR /app

# Install revue-io from PyPI (or local build during dev)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipe.sh /app/pipe.sh
RUN chmod +x /app/pipe.sh

ENTRYPOINT ["/app/pipe.sh"]
