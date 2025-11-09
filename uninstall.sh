#!/bin/bash

# --- Кольори для красивого виводу ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Починаю видалення Telegram Linux Monitor Bot... ===${NC}"

SERVICE_NAME="telegram-linux-monitor.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

# --- Перевірка, чи запущений скрипт з правами sudo ---
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Помилка: будь ласка, запустіть цей скрипт з правами sudo.${NC}"
  echo "Наприклад: sudo bash uninstall.sh"
  exit 1
fi

# --- Перевірка, чи існує systemd ---
if ! command -v systemctl &> /dev/null; then
    echo -e "${GREEN}Systemd не знайдено. Пропускаю крок видалення сервісу.${NC}"
    echo "Видалення завершено. Ви можете видалити папку з проєктом."
    exit 0
fi

# --- Перевірка, чи існує сам файл сервісу ---
if [ ! -f "$SERVICE_FILE" ]; then
    echo -e "${GREEN}Сервіс '$SERVICE_NAME' не знайдено. Можливо, він вже видалений.${NC}"
    echo "Видалення завершено. Ви можете видалити папку з проєктом."
    exit 0
fi

echo "Зупиняю сервіс..."
systemctl stop "$SERVICE_NAME"

echo "Вимикаю сервіс з автозапуску..."
systemctl disable "$SERVICE_NAME"

echo "Видаляю файл сервісу..."
rm "$SERVICE_FILE"

echo "Перезавантажую конфігурацію systemd..."
systemctl daemon-reload

echo ""
echo -e "${GREEN}=== Бот успішно видалений з автозапуску! ===${NC}"
echo "Тепер ви можете безпечно видалити папку з проєктом."
echo "Наприклад: cd .. && rm -rf $(basename "$PWD")"