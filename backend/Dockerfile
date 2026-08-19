# PathGuard AI backend — deployed image for Railway / Render.
#
# TensorFlow is deliberately absent (see requirements.txt) and the LSTM
# destination router is not mounted. Measured result: 979 MB, down from ~1.6 GB
# with TF. The remaining bulk is scipy/scikit-learn/pandas plus matplotlib, which
# arrives as a hard dependency of filterpy (the Kalman library) even though only
# its KalmanFilter class is used.
FROM python:3.13-slim

# psycopg2-binary and the scientific wheels are prebuilt for 3.13, so no compiler
# is needed — libpq is still required at runtime for psycopg2.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Railway and Render inject the port to bind; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

# Entry point is scripts.demo_server, not app.main. It mounts every router that
# app.main does and additionally serves dashboard.html plus the read-only /demo
# endpoints the page needs — which is what week 1's gate is checked against
# ("walk around the block, see your own track on the web dashboard").
# Once /demo/track and /demo/alerts are promoted to app/api/tracking.py and
# alerts.py, switch this to `app.main:app` and drop `COPY scripts`.
#
# Single worker: app/services/kalman_filter.py keeps per-patient filter state in
# process memory, so a second worker would smooth a patient's track against a
# filter that has seen only half their readings.
CMD ["sh", "-c", "uvicorn scripts.demo_server:app --host 0.0.0.0 --port ${PORT} --workers 1"]
