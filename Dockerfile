# Use an official Python runtime as a parent image
FROM python:3.12-alpine

# Set the working directory
WORKDIR /app

# Copy the application
COPY app.py requirements.txt ./
COPY templates templates/
COPY static static/

# Install dependencies
RUN pip install -r requirements.txt

# Create config directory and change permissions
RUN mkdir -p /epub
RUN mkdir -p /logs
RUN chmod -R 777 /logs # Probably not needed


# Run the application
ENV JNCEP_OUTPUT=/epub
CMD ["python", "-m", "app"]