FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY *.py /app/
RUN pip install --no-cache-dir qrcode==7.4.2
VOLUME /data
EXPOSE 8080
CMD ["python", "-u", "launch.py"]
