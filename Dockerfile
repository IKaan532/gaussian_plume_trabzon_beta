FROM python:3.10-slim


RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        libgomp1 \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip wheel setuptools

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api_module.py   .
COPY model.py        .
COPY scenarios.py    .
COPY sources.py      .
COPY validation.py   .
COPY visualization.py .
COPY app.py          .
COPY main.py         .

ENV OWM_API_KEY=""

ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

ENV MPLBACKEND=Agg

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
