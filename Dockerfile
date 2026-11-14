FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY *.py /app/
VOLUME /data
CMD ["python", "-u", "app.py"]
