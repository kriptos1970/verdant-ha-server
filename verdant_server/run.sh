#!/usr/bin/with-contenv bashio
set -e

VERDANT_TOKEN="$(bashio::config 'token')"
if [[ -z "${VERDANT_TOKEN}" ]]; then
    bashio::log.fatal "Configura un token prima di avviare Verdant Server."
    exit 1
fi

export VERDANT_TOKEN
export VERDANT_LOG_LEVEL="$(bashio::config 'log_level')"
export VERDANT_MAX_PHOTO_MB="$(bashio::config 'max_photo_mb')"
export VERDANT_EXPOSED_ENTITIES="$(bashio::config 'exposed_entities')"
export VERDANT_DATA_DIR="/data"
export VERDANT_PORT="8099"

bashio::log.info "Avvio Verdant Server sulla porta ${VERDANT_PORT}."
exec uvicorn main:app --host 0.0.0.0 --port "${VERDANT_PORT}" --log-level "${VERDANT_LOG_LEVEL}"
