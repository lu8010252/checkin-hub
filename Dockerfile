FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY *.py /app/
VOLUME /data
EXPOSE 8080
CMD ["python", "-u", "app.py"]
