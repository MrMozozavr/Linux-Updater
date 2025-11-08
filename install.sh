#!/bin/bash

# --- Кольори для красивого виводу ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Ласкаво просимо до інсталятора Telegram Linux Monitor Bot! ===${NC}"
echo "Цей скрипт налаштує бота та додасть його в автозапуск."
echo ""

# --- Крок 1: Перевірка базових залежностей ---
echo -e "${YELLOW}> Крок 1: Перевірка Python та pip...${NC}"
if ! command -v python &> /dev/null || ! python -m pip --version &> /dev/null; then
    echo -e "${RED}Помилка: Python 3 або pip не знайдено.${NC}"
    echo "Будь ласка, встановіть їх за допомогою вашого пакетного менеджера."
    echo "Наприклад: sudo pacman -S python-pip або sudo apt install python3-pip"
    exit 1
fi
echo "Python та pip знайдено."
echo ""

# --- Крок 2: Створення віртуального середовища та встановлення бібліотек ---
PROJECT_DIR=$(pwd)
VENV_DIR="$PROJECT_DIR/.venv"

echo -e "${YELLOW}> Крок 2: Налаштування віртуального середовища...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    echo "Створюю віртуальне середовище в '$VENV_DIR'..."
    python -m venv .venv
else
    echo "Віртуальне середовище вже існує."
fi

echo "Встановлюю бібліотеки з файлу requirements.txt..."
# Активуємо середовище і встановлюємо залежності з файлу
source "$VENV_DIR/bin/activate"
pip install -r requirements.txt &> /dev/null
deactivate

# Перевіряємо, чи був попередній крок успішним
if [ $? -ne 0 ]; then
    echo -e "${RED}Помилка: Не вдалося встановити бібліотеки. Перевірте файл requirements.txt та з'єднання з інтернетом.${NC}"
    exit 1
fi
echo "Бібліотеки успішно встановлено."
echo ""

# --- Крок 3: Створення файлу .env ---
echo -e "${YELLOW}> Крок 3: Налаштування конфігурації...${NC}"
if [ -f ".env" ]; then
    echo "Файл .env вже існує. Пропускаю цей крок."
else
    read -p "Введіть ваш TELEGRAM_API_TOKEN (отриманий від @BotFather): " API_TOKEN
    read -p "Введіть ваш ALLOWED_USER_ID (дізнайтеся у @userinfobot): " ALLOWED_USER_ID

    # Створюємо файл .env
    echo "TELEGRAM_API_TOKEN=$API_TOKEN" > .env
    echo "ALLOWED_USER_ID=$ALLOWED_USER_ID" >> .env
    echo "Файл .env успішно створено."
fi
echo ""

# --- Крок 4: Налаштування автозапуску через systemd ---
echo -e "${YELLOW}> Крок 4: Налаштування автозапуску (systemd)...${NC}"
if ! command -v systemctl &> /dev/null; then
    echo -e "${RED}Помилка: Systemd не знайдено.${NC}"
    echo "На жаль, автоматичне налаштування автозапуску можливе тільки для систем з systemd."
    echo "Ви можете запускати бота вручну командою: '$VENV_DIR/bin/python $PROJECT_DIR/linux_monitor_bot.py'"
    exit 1
fi

SERVICE_NAME="telegram-linux-monitor.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"
CURRENT_USER=$(whoami)
PYTHON_PATH="$VENV_DIR/bin/python"
SCRIPT_PATH="$PROJECT_DIR/linux_monitor_bot.py"

echo "Створюю файл сервісу systemd..."
# Використовуємо sudo tee для запису у системну директорію
cat << EOF | sudo tee "$SERVICE_FILE" > /dev/null
[Unit]
Description=Telegram Bot for Linux System Monitoring
After=network-online.target
Wants=network-online.target

[Service]
User=$CURRENT_USER
Group=$(id -gn "$CURRENT_USER")
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_PATH $SCRIPT_PATH
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "Перезавантажую конфігурацію systemd..."
sudo systemctl daemon-reload

echo "Вмикаю сервіс для автозапуску..."
sudo systemctl enable "$SERVICE_NAME"

echo "Запускаю/перезапускаю сервіс..."
sudo systemctl restart "$SERVICE_NAME" # Використовуємо restart, щоб гарантовано застосувати зміни
echo ""

# --- Фінальні інструкції ---
echo -e "${GREEN}=== Встановлення завершено! ===${NC}"
echo "Бот запущений і доданий в автозапуск."
echo ""
echo "Корисні команди:"
echo -e "  - Перевірити статус бота: ${YELLOW}sudo systemctl status $SERVICE_NAME${NC}"
echo -e "  - Переглянути логи бота: ${YELLOW}sudo journalctl -u $SERVICE_NAME -f${NC}"
echo -e "  - Зупинити бота: ${YELLOW}sudo systemctl stop $SERVICE_NAME${NC}"
echo -e "  - Перезапустити бота: ${YELLOW}sudo systemctl restart $SERVICE_NAME${NC}"
echo ""
echo "Напишіть /start вашому боту в Telegram, щоб почати!"