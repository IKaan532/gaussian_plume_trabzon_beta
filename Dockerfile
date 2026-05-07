# ── Gaussian Plume Dispersion Model — Production Dockerfile ───────────────────
# Base image: slim Python 3.10
FROM python:3.10-slim

# Metadata
LABEL maintainer="gaussian-plume-trabzon"
LABEL description="Air quality Gaussian plume simulation — Trabzon pilot"

# System dependencies for matplotlib/Pillow (no GUI, headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only necessary application files
COPY api_module.py   .
COPY model.py        .
COPY scenarios.py    .
COPY sources.py      .
COPY validation.py   .
COPY visualization.py .
COPY app.py          .
COPY main.py         .

# API key injected at runtime — never hardcoded in the image.
# Pass it with: docker run -e OWM_API_KEY=your_key …
ENV OWM_API_KEY=""

# Streamlit settings (headless, no browser auto-open)
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Matplotlib non-interactive backend
ENV MPLBACKEND=Agg

# Expose Streamlit default port
EXPOSE 8501

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

# Entry point — run the Streamlit web app
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
