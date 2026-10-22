FROM python:3.10-slim

# Create user with UID 1000 (standard requirement for Hugging Face Spaces)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PORT=7860 \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

# Copy project files
COPY --chown=user:user . $HOME/app

# Hugging Face Spaces default port
EXPOSE 7860

# Launch review_app.py (handles auto-unzip of cs_dataset.zip if present)
CMD ["python", "review_app.py", "--port", "7860"]
