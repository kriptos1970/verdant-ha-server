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
# bashio can render array items on separate lines. Re-encode the value as
# compact JSON so the Python process always receives the complete list.
export VERDANT_EXPOSED_ENTITIES="$(bashio::config 'exposed_entities' | jq -c '.')"
export VERDANT_POLL_INTERVAL_MINUTES="$(bashio::config 'poll_interval_minutes')"
export VERDANT_AI_PROVIDER="$(bashio::config 'ai_provider')"
export VERDANT_GEMINI_API_KEY="$(bashio::config 'gemini_api_key')"
export VERDANT_OPENAI_API_KEY="$(bashio::config 'openai_api_key')"
if bashio::config.has_value 'ai_plan_frequency_days'; then
    export VERDANT_AI_PLAN_FREQUENCY_DAYS="$(bashio::config 'ai_plan_frequency_days')"
else
    export VERDANT_AI_PLAN_FREQUENCY_DAYS="1"
fi
if bashio::config.has_value 'ai_plan_update_time'; then
    export VERDANT_AI_PLAN_UPDATE_TIME="$(bashio::config 'ai_plan_update_time')"
else
    export VERDANT_AI_PLAN_UPDATE_TIME="03:00"
fi
export VERDANT_DATA_DIR="/data"
export VERDANT_PORT="8099"

bashio::log.info "Avvio Verdant Server sulla porta ${VERDANT_PORT}."
exec uvicorn main:app --host 0.0.0.0 --port "${VERDANT_PORT}" --log-level "${VERDANT_LOG_LEVEL}"
