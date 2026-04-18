FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY auth.py bao.py etrade_client.py mcp_server.py ./

ENV PYTHONUNBUFFERED=1

EXPOSE 8767

CMD ["python", "mcp_server.py"]
