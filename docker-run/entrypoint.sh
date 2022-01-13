#!/usr/bin/env bash

env | grep -v '^#' | xargs >docker-run-action.env

if [[ -n $INPUT_USERNAME ]]; then
  echo $INPUT_PASSWORD | docker login $INPUT_REGISTRY -u $INPUT_USERNAME --password-stdin
fi

if [[ -n $INPUT_DOCKER_NETWORK ]]; then
  INPUT_OPTIONS="$INPUT_OPTIONS --network $INPUT_DOCKER_NETWORK"
fi

exec docker run --env-file docker-run-action.env --workdir "$INPUT_WORKDIR" -v "/var/run/docker.sock:/var/run/docker.sock" -v "$RUNNER_WORKSPACE:/github/workspace" $INPUT_OPTIONS --entrypoint=$INPUT_SHELL $INPUT_IMAGE -c "${INPUT_RUN//$'\n'/;}"
