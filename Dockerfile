FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && useradd --uid 10001 --create-home app
COPY --chown=app:app scope_search/ ./scope_search/
USER app
EXPOSE 8894
ENTRYPOINT ["python", "-m", "scope_search"]
CMD ["serve", "--host", "0.0.0.0"]
