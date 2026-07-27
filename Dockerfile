FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BLACKFONG_HUD_HOST=0.0.0.0 \
    BLACKFONG_HUD_PORT=8080 \
    BLACKFONG_HUD_OPEN_BROWSER=false \
    BLACKFONG_WINDOWS_TOASTS_ENABLED=false \
    BLACKFONG_TINYPROGRAMMER_ENABLED=false \
    BLACKFONG_OLLAMA_URL=http://host.docker.internal:11434

WORKDIR /app

COPY requirements.docker-hud.txt ./requirements.docker-hud.txt
RUN pip install --no-cache-dir -r requirements.docker-hud.txt

COPY blackfong_ecosystem ./blackfong_ecosystem
COPY desktop_shell.py ./desktop_shell.py
COPY README.md ./README.md
COPY ["OBEOS user guide.md", "./OBEOS-user-guide.md"]

EXPOSE 8080

CMD ["python", "-m", "blackfong_ecosystem.cli", "hud", "--host", "0.0.0.0", "--port", "8080", "--no-open-browser"]
