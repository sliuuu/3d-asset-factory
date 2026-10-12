# Hub API + UI + CPU worker (ai-server, x86).
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN useradd -u 10001 -m hub && mkdir -p /var/hub && chown hub /var/hub
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hub/ hub/
COPY catalog/ catalog/
COPY --from=web /web/dist web/dist
USER hub
ENV HUB_DATA_DIR=/var/hub
EXPOSE 8080
CMD ["uvicorn", "hub.api:app", "--host", "0.0.0.0", "--port", "8080"]
