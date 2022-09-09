# syntax=docker/dockerfile:1
FROM python:3.10-slim-bullseye
WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
COPY app.py app.py
COPY templates templates/
COPY static static/
RUN mkdir -p /epub
RUN mkdir -p /logs
ENV JNCEP_OUTPUT=/epub
CMD [ "python3", "-m" , "app"]