#!/bin/bash

# Скрипт для установки Capsula Assistant как системного сервиса
# Запускать от имени root

set -e

SERVICE_NAME="capsula-assistant"
SERVICE_FILE="capsula-assistant.service"
PROJECT_DIR="/Capsula_assist/capsula_assist"
VENV_DIR="/Capsula_assist/capsula_assist/venv"

echo "🤖 Установка Capsula Assistant как системного сервиса..."

# Проверяем, что скрипт запущен от root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Этот скрипт должен быть запущен от имени root"
    echo "Используйте: sudo $0"
    exit 1
fi

# Проверяем существование директорий
if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ Директория проекта не найдена: $PROJECT_DIR"
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "❌ Виртуальное окружение не найдено: $VENV_DIR"
    exit 1
fi

if [ ! -f "$PROJECT_DIR/run.py" ]; then
    echo "❌ Файл run.py не найден в $PROJECT_DIR"
    exit 1
fi

# Проверяем наличие .env файла
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "⚠️  Файл .env не найден в $PROJECT_DIR"
    echo "📝 Создайте файл .env со следующим содержимым:"
    echo "BOT_TOKEN=your_telegram_bot_token_here"
    echo "DEFAULT_TIMEZONE=Europe/Moscow  # опционально"
    echo ""
    read -p "Продолжить установку? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Копируем service файл
echo "📋 Копирование service файла..."
cp "$SERVICE_FILE" "/etc/systemd/system/$SERVICE_NAME.service"

# Перезагружаем systemd
echo "🔄 Перезагрузка systemd..."
systemctl daemon-reload

# Включаем автозапуск
echo "✅ Включение автозапуска..."
systemctl enable "$SERVICE_NAME"

# Запускаем сервис
echo "🚀 Запуск сервиса..."
systemctl start "$SERVICE_NAME"

# Проверяем статус
echo "📊 Проверка статуса сервиса..."
sleep 2
systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "✅ Установка завершена!"
echo ""
echo "📋 Полезные команды:"
echo "  Статус сервиса:    systemctl status $SERVICE_NAME"
echo "  Остановить:        systemctl stop $SERVICE_NAME"
echo "  Запустить:         systemctl start $SERVICE_NAME"
echo "  Перезапустить:     systemctl restart $SERVICE_NAME"
echo "  Логи:              journalctl -u $SERVICE_NAME -f"
echo "  Отключить автозапуск: systemctl disable $SERVICE_NAME"
echo ""
echo "📝 Логи сервиса сохраняются в systemd journal"
echo "   Для просмотра: journalctl -u $SERVICE_NAME"
