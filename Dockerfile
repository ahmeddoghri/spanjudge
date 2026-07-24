FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
EXPOSE 4318
CMD ["spanjudge", "--database", "/data/spanjudge.db", "serve", "--host", "0.0.0.0", "--port", "4318"]
