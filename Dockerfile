FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1 STATE_PATH=/data/state.json
VOLUME /data
# One process: mailbox-connected orchestrator + internal agents. Mailbox must be connected once beforehand.
CMD ["python", "-m", "agents.run_all"]
