# multi-stage build could be added for optimization
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# system deps
# ffmpeg was missing here entirely - every ffmpeg subprocess call in this
# codebase (thumbnail generation in media_utils.py, NudeNet video-frame
# sampling in apps/media/safety.py, the new perceptual-fingerprint scan
# below) was silently failing with FileNotFoundError in every production
# container, caught by a bare except and swallowed. Thumbnails: silently
# never generated. Video safety scans: silently fell back to
# pending_review/quarantine for every single video upload, not a bypass,
# but likely explains a permanently-growing moderation queue.
RUN apt-get update && apt-get install -y build-essential libpq-dev git curl ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements /app/requirements
RUN pip install --upgrade pip
RUN pip install -r requirements/base.txt

COPY . /app

# create media and static folders
RUN mkdir -p /vol/web/media /vol/web/static

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
