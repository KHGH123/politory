FROM python:3.11-slim

WORKDIR /app

COPY mcp_server/requirements.txt ./mcp_server/requirements.txt
RUN pip install --no-cache-dir -r mcp_server/requirements.txt

COPY rag ./rag
COPY mcp_server ./mcp_server

CMD ["python", "-m", "mcp_server.server"]
