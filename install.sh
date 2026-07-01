#!/usr/bin/env bash
#
# Автоустановщик Telegram-бота модерации.
# Запуск одной строкой на чистом VPS (Ubuntu/Debian):
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/FnoUp/tg-mod-bot/main/install.sh)
#
# Скрипт сам: поставит git/docker, склонирует репозиторий, спросит токен и
# admin ID прямо в терминале, создаст .env и запустит бота через docker compose.
#
set -euo pipefail

# --- Настройки (можно переопределить переменными окружения) ---
REPO_URL="${REPO_URL:-https://github.com/FnoUp/tg-mod-bot.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tg-mod-bot}"
BRANCH="${BRANCH:-main}"

# Все интерактивные вопросы читаем из терминала (/dev/tty), чтобы работало
# даже при запуске через `curl ... | bash`.
TTY=/dev/tty

info()  { printf '\033[1;36m[*]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()   { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }

ask() {
    # ask "Вопрос" "значение_по_умолчанию" -> печатает ответ в stdout (для да/нет)
    local prompt="$1" default="${2:-}" answer=""
    if [ -n "$default" ]; then
        printf '%s [%s]: ' "$prompt" "$default" > "$TTY"
    else
        printf '%s: ' "$prompt" > "$TTY"
    fi
    read -r answer < "$TTY" || true
    echo "${answer:-$default}"
}

# prompt_required <varname> <текст> <regex> <подсказка при ошибке>
# Читает, пока значение не пройдёт валидацию. При EOF (нет терминала) — выходит.
prompt_required() {
    local __var="$1" prompt="$2" re="$3" msg="$4" val=""
    while :; do
        printf '%s: ' "$prompt" > "$TTY"
        if ! IFS= read -r val < "$TTY"; then
            err "Ввод прерван (нет доступного терминала). Запусти скрипт в интерактивной SSH-сессии."
            exit 1
        fi
        if printf '%s' "$val" | grep -Eq "$re"; then
            printf -v "$__var" '%s' "$val"
            return 0
        fi
        warn "$msg"
    done
}

# prompt_optional <varname> <текст> <regex> <подсказка>. Пустой ввод = пропуск.
prompt_optional() {
    local __var="$1" prompt="$2" re="$3" msg="$4" val=""
    while :; do
        printf '%s: ' "$prompt" > "$TTY"
        IFS= read -r val < "$TTY" || val=""
        if [ -z "$val" ]; then
            printf -v "$__var" '%s' ""
            return 0
        fi
        if printf '%s' "$val" | grep -Eq "$re"; then
            printf -v "$__var" '%s' "$val"
            return 0
        fi
        warn "$msg"
    done
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "Запусти скрипт от root (или через sudo)."
        exit 1
    fi
}

install_prereqs() {
    if ! command -v git >/dev/null 2>&1; then
        info "Устанавливаю git..."
        apt-get update -y && apt-get install -y git
    fi
    if ! command -v docker >/dev/null 2>&1; then
        info "Устанавливаю Docker..."
        curl -fsSL https://get.docker.com | sh
    fi
    if ! docker compose version >/dev/null 2>&1; then
        info "Устанавливаю Docker Compose plugin..."
        apt-get update -y && apt-get install -y docker-compose-plugin
    fi
    ok "Зависимости на месте."
}

clone_or_update() {
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Репозиторий уже есть — обновляю ($INSTALL_DIR)..."
        git -C "$INSTALL_DIR" pull --ff-only
    else
        info "Клонирую $REPO_URL -> $INSTALL_DIR ..."
        git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    fi
}

configure_env() {
    cd "$INSTALL_DIR"

    if [ -f .env ]; then
        local overwrite
        overwrite=$(ask "Файл .env уже существует. Перенастроить заново? (y/N)" "N")
        case "$overwrite" in
            y|Y|yes|YES) ;;
            *) ok "Оставляю текущий .env без изменений."; return 0 ;;
        esac
        cp .env ".env.bak.$(date +%F_%H-%M-%S)"
    fi

    cp .env.example .env

    echo > "$TTY"
    info "Заполним настройки бота (Enter — оставить пустым/по умолчанию)." > "$TTY"

    local token="" admins="" logchat=""
    prompt_required token \
        "Токен бота от @BotFather (например 12345:AAE...)" \
        '^[0-9]+:[A-Za-z0-9_-]+$' \
        "Неверный формат. Пример: 12345678:AAExxxxxxxxxxxxxxxxxxxxxxxx"
    prompt_required admins \
        "Твой Telegram ID (узнать: @userinfobot); несколько — через запятую" \
        '^[0-9]+(,[0-9]+)*$' \
        "Только цифры и запятые, например: 781234567 или 781234567,781234568"
    prompt_optional logchat \
        "ID лог-чата для истории модерации (Enter — пропустить)" \
        '^-?[0-9]+$' \
        "Это должно быть число, например -1001234567890. Или Enter, чтобы пропустить."

    # Все значения провалидированы (цифры/буквы/':' /',' /'-'), спецсимволов для sed
    # нет — пишем через безопасный разделитель '|' без экранирования.
    _set() {
        local key="$1" val="$2"
        sed -i "s|^${key}=.*|${key}=${val}|" .env
    }
    _set BOT_TOKEN "$token"
    _set ADMIN_IDS "$admins"
    [ -n "$logchat" ] && _set LOG_CHAT_ID "$logchat"

    ok ".env настроен."
}

launch() {
    cd "$INSTALL_DIR"
    mkdir -p data
    info "Собираю и запускаю бота..."
    docker compose up -d --build
    ok "Бот запущен."
    echo
    ok "Дальше:"
    echo "  1) Добавь бота в чат и сделай админом (права: удалять сообщения, банить, ограничивать)."
    echo "  2) Напиши боту в личку /start — иначе он не сможет присылать уведомления."
    echo "  3) Настрой тексты и модули командой /panel в личке бота."
    echo
    echo "  Логи:        docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
    echo "  Перезапуск:  docker compose -f $INSTALL_DIR/docker-compose.yml restart"
    echo "  Обновить:    перезапусти этот install.sh (сделает git pull + пересборку)"
}

main() {
    require_root
    install_prereqs
    clone_or_update
    configure_env
    launch
}

main "$@"
