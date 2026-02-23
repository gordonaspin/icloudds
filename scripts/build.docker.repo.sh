#!/bin/bash
set -euo pipefail

OWNER=gordonaspin
PROJECT=$(basename $(pwd))
REPO=https://github.com/${OWNER}/${PROJECT}.git
VERSION="$(cat pyproject.toml | grep version | cut -d'"' -f 2)"
REMOTE_HASH=$(git ls-remote ${REPO} HEAD | awk '{ print $1 }')
echo "Repo: ${OWNER}"
echo "Project: ${PROJECT}"
echo "Current ${PROJECT} version: ${VERSION}"
echo "Git remote hash: ${REMOTE_HASH}"

docker build \
  --build-arg CACHE_BUST=${REMOTE_HASH} \
  --build-arg PROJECT=${PROJECT} \
  --build-arg REPO=${REPO} \
  --progress plain \
  -t "${OWNER}/${PROJECT}:${VERSION}" \
  -f "Dockerfile" \
  .

docker tag "${OWNER}/${PROJECT}:${VERSION}" "${OWNER}/${PROJECT}:latest"
docker push -a ${OWNER}/${PROJECT}
