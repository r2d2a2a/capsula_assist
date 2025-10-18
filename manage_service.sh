#!/bin/bash

# Скрипт для управления сервисом Capsula Assistant
# Можно запускать от обычного пользователя (команды с sudo)

SERVICE_NAME="capsula-assistant"

show_help() {
    echo "🤖 Управление сервисом Capsula Assistant"
    echo ""
    echo "Использование: $0 [команда]"
    echo ""
    echo "Команды:"
    echo "  start     - Запустить сервис"
    echo "  stop      - Остановить сервис"
    echo "  restart   - Перезапустить сервис"
    echo "  status    - Показать статус сервиса"
    echo "  logs      - Показать логи сервиса"
    echo "  logs-f    - Показать логи в реальном времени"
    echo "  enable    - Включить автозапуск"
    echo "  disable   - Отключить автозапуск"
    echo "  uninstall - Удалить сервис"
    echo "  help      - Показать эту справку"
    echo ""
}

case "$1" in
    start)
        echo "🚀 Запуск сервиса $SERVICE_NAME..."
        sudo systemctl start "$SERVICE_NAME"
        sudo systemctl status "$SERVICE_NAME" --no-pager
        ;;
    stop)
        echo "⏹️  Остановка сервиса $SERVICE_NAME..."
        sudo systemctl stop "$SERVICE_NAME"
        sudo systemctl status "$SERVICE_NAME" --no-pager
        ;;
    restart)
        echo "🔄 Перезапуск сервиса $SERVICE_NAME..."
        sudo systemctl restart "$SERVICE_NAME"
        sudo systemctl status "$SERVICE_NAME" --no-pager
        ;;
    status)
        echo "📊 Статус сервиса $SERVICE_NAME:"
        sudo systemctl status "$SERVICE_NAME" --no-pager
        ;;
    logs)
        echo "📝 Логи сервиса $SERVICE_NAME:"
        sudo journalctl -u "$SERVICE_NAME" --no-pager
        ;;
    logs-f)
        echo "📝 Логи сервиса $SERVICE_NAME (в реальном времени):"
        echo "Нажмите Ctrl+C для выхода"
        sudo journalctl -u "$SERVICE_NAME" -f
        ;;
    enable)
        echo "✅ Включение автозапуска сервиса $SERVICE_NAME..."
        sudo systemctl enable "$SERVICE_NAME"
        echo "Автозапуск включен"
        ;;
    disable)
        echo "❌ Отключение автозапуска сервиса $SERVICE_NAME..."
        sudo systemctl disable "$SERVICE_NAME"
        echo "Автозапуск отключен"
        ;;
    uninstall)
        echo "🗑️  Удаление сервиса $SERVICE_NAME..."
        read -p "Вы уверены? Это остановит и удалит сервис (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
            sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
            sudo rm -f "/etc/systemd/system/$SERVICE_NAME.service"
            sudo systemctl daemon-reload
            echo "✅ Сервис удален"
        else
            echo "❌ Удаление отменено"
        fi
        ;;
    help|--help|-h)
        show_help
        ;;
    "")
        echo "❌ Не указана команда"
        show_help
        exit 1
        ;;
    *)
        echo "❌ Неизвестная команда: $1"
        show_help
        exit 1
        ;;
esac
