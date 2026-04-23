FROM node:22-slim AS node-base

FROM python:3.12-slim-bookworm

COPY --from=node-base /usr/local/bin/node /usr/local/bin/node
COPY --from=node-base /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /home/appuser/.npm \
    && chown -R appuser:appuser /home/appuser

ENV HOME=/home/appuser

# Pre-cache the MCP package as appuser so it is available at runtime
RUN su appuser -c "npx -y @openbnb/mcp-server-airbnb --help" || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
