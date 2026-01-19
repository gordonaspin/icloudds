FROM python:3.14-alpine AS base

FROM base AS builder

RUN set -xe \
  && apk add tzdata bash vim git \
  && python -m ensurepip \
  && pip install --upgrade pip setuptools \
  && pip install wheel==0.35.1 \
  && pip install git+https://github.com/gordonaspin/icloudds.git \
  && pip list \
  && icloud -h \
  && icloudds -h 

ARG TZ="America/New_York"
RUN cp /usr/share/zoneinfo/$TZ /etc/localtime
ENV TZ=${TZ}

ARG GROUP_NAME=docker
ARG USER_NAME=docker
ARG USER_UID=1000
ARG GROUP_GID=1000

# Create a group and a user, then add the user to the group
RUN addgroup -g ${GROUP_GID} -S ${GROUP_NAME} && \
    adduser -u ${USER_UID} -S -G ${GROUP_NAME} -D -H ${USER_NAME}
USER docker

WORKDIR /home/docker
COPY --chown=docker:docker .ignore*.txt .
COPY --chown=docker:docker .include*.txt .
COPY --chown=docker:docker logging-config.json .

ENTRYPOINT [ "icloudds", "-d", "/drive", "--cookie-directory", "/cookies" ]
