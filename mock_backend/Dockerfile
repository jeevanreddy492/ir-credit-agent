FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.6 "uvicorn[standard]==0.34.0"
COPY mock_backend ./mock_backend
EXPOSE 9000
CMD ["uvicorn", "mock_backend.main:app", "--host", "0.0.0.0", "--port", "9000"]
