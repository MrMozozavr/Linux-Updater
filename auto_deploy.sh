#!/bin/bash
set -e

# Кольори для виводу
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Автоматичне налаштування Linux Monitor Bot (Debian/Ubuntu) ===${NC}"

# 1. Запит даних у користувача
echo -e "\n${BLUE}[1/4] Налаштування змінних оточення...${NC}"
if [ ! -f .env ]; then
    read -p "Введіть TELEGRAM_API_TOKEN: " bot_token
    read -p "Введіть ваш ALLOWED_USER_ID: " user_id

    cat > .env <<EOL
TELEGRAM_API_TOKEN=${bot_token}
ALLOWED_USER_ID=${user_id}
EOL
    echo -e "${GREEN}Файл .env створено!${NC}"
else
    echo ".env файл вже існує. Використовую його."
fi

# 2. Встановлення системних пакетів
echo -e "\n${BLUE}[2/4] Встановлення залежностей Debian/Ubuntu...${NC}"
SUDO=""
if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
fi
$SUDO apt-get update
$SUDO apt-get install -y python3 python3-pip python3-venv

# 3. Налаштування Python
echo -e "\n${BLUE}[3/4] Налаштування Python віртуального середовища...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    # Резервне встановлення базових пакетів, якщо файлу немає
    pip install aiogram psutil aiohttp python-dotenv
fi

echo -e "${GREEN}Залежності Python встановлено!${NC}"

# 4. Створення systemd сервісу (system-wide)
echo -e "\n${BLUE}[4/4] Створення systemd сервісу для фонової роботи...${NC}"

WORK_DIR=$(pwd)
CURRENT_USER=$USER
if [ "$CURRENT_USER" = "root" ]; then
    # Намагаємось знайти реального користувача, якщо скрипт запущено через sudo
    if [ -n "$SUDO_USER" ]; then
        CURRENT_USER=$SUDO_USER
    fi
fi

# Зупиняємо старий сервіс, якщо він існує (щоб чисто оновити)
echo -e "${BLUE}Оновлення конфігурації сервісу...${NC}"
$SUDO systemctl stop linux-monitor.service 2>/dev/null || true

# Сервіс Linux Monitor бота
$SUDO bash -c "cat > /etc/systemd/system/linux-monitor.service" <<EOL
[Unit]
Description=Telegram Linux Monitor Bot
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${WORK_DIR}
ExecStart=${WORK_DIR}/venv/bin/python3 linux_monitor_bot.py
Restart=always
RestartSec=5
Environment="PATH=${WORK_DIR}/venv/bin:%E/PATH"

[Install]
WantedBy=multi-user.target
EOL

$SUDO systemctl daemon-reload
$SUDO systemctl enable linux-monitor.service
$SUDO systemctl restart linux-monitor.service

echo -e "\n${GREEN}======================================================================${NC}"
echo -e "${GREEN}ГОТОВО! Ваш Linux Monitor Bot успішно встановлений та запущений у фоні.${NC}"
echo -e "======================================================================"
echo -e "🔄 Бот автоматично запуститься після перезавантаження сервера."
echo -e ""
echo -e "📋 Перевірити статус бота:        ${BLUE}sudo systemctl status linux-monitor${NC}"
echo -e "📋 Подивитись логи бота:          ${BLUE}sudo journalctl -u linux-monitor -f${NC}"
