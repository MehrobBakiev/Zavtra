FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home zavtra \
    && mkdir -p /app/data && chown -R zavtra:zavtra /app
USER zavtra
CMD ["python", "-m", "zavtra", "bot"]
