#!/bin/bash

# Скрипт для мониторинга сервиса Capsula Assistant
# Проверяет состояние сервиса и отправляет уведомления при проблемах

SERVICE_NAME="capsula-assistant"
LOG_FILE="/var/log/capsula-assistant/monitor.log"
MAX_RESTART_ATTEMPTS=3
RESTART_COUNT_FILE="/tmp/capsula_restart_count"

# Создаем директорию для логов если не существует
sudo mkdir -p /var/log/capsula-assistant
sudo chown root:root /var/log/capsula-assistant

log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | sudo tee -a "$LOG_FILE"
}

check_service_status() {
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        return 0  # Сервис работает
    else
        return 1  # Сервис не работает
    fi
}

restart_service() {
    log_message "Попытка перезапуска сервиса $SERVICE_NAME"
    sudo systemctl restart "$SERVICE_NAME"
    sleep 5
    
    if check_service_status; then
        log_message "Сервис $SERVICE_NAME успешно перезапущен"
        echo "0" | sudo tee "$RESTART_COUNT_FILE" > /dev/null
        return 0
    else
        log_message "ОШИБКА: Не удалось перезапустить сервис $SERVICE_NAME"
        return 1
    fi
}

get_restart_count() {
    if [ -f "$RESTART_COUNT_FILE" ]; then
        cat "$RESTART_COUNT_FILE"
    else
        echo "0"
    fi
}

increment_restart_count() {
    local count=$(get_restart_count)
    echo $((count + 1)) | sudo tee "$RESTART_COUNT_FILE" > /dev/null
}

reset_restart_count() {
    echo "0" | sudo tee "$RESTART_COUNT_FILE" > /dev/null
}

# Основная логика мониторинга
main() {
    log_message "Проверка состояния сервиса $SERVICE_NAME"
    
    if check_service_status; then
        log_message "Сервис $SERVICE_NAME работает нормально"
        reset_restart_count
    else
        log_message "ПРЕДУПРЕЖДЕНИЕ: Сервис $SERVICE_NAME не работает"
        
        local restart_count=$(get_restart_count)
        
        if [ "$restart_count" -lt "$MAX_RESTART_ATTEMPTS" ]; then
            log_message "Попытка автоматического восстановления (попытка $((restart_count + 1))/$MAX_RESTART_ATTEMPTS)"
            
            if restart_service; then
                log_message "Сервис восстановлен автоматически"
            else
                increment_restart_count
                log_message "Не удалось восстановить сервис автоматически"
            fi
        else
            log_message "КРИТИЧЕСКАЯ ОШИБКА: Превышено максимальное количество попыток перезапуска"
            log_message "Требуется ручное вмешательство администратора"
            
            # Здесь можно добавить отправку уведомлений
            # Например, email или webhook
        fi
    fi
}

# Показать справку
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Мониторинг сервиса Capsula Assistant"
    echo ""
    echo "Использование: $0 [опции]"
    echo ""
    echo "Опции:"
    echo "  --help, -h    Показать эту справку"
    echo "  --status      Показать текущий статус"
    echo "  --logs        Показать логи мониторинга"
    echo "  --reset       Сбросить счетчик перезапусков"
    echo ""
    echo "Для автоматического мониторинга добавьте в crontab:"
    echo "*/5 * * * * /path/to/monitor_service.sh"
    exit 0
fi

# Показать статус
if [ "$1" = "--status" ]; then
    echo "Статус сервиса $SERVICE_NAME:"
    sudo systemctl status "$SERVICE_NAME" --no-pager
    echo ""
    echo "Количество неудачных попыток перезапуска: $(get_restart_count)"
    exit 0
fi

# Показать логи
if [ "$1" = "--logs" ]; then
    if [ -f "$LOG_FILE" ]; then
        sudo tail -50 "$LOG_FILE"
    else
        echo "Лог файл не найден: $LOG_FILE"
    fi
    exit 0
fi

# Сбросить счетчик
if [ "$1" = "--reset" ]; then
    reset_restart_count
    echo "Счетчик перезапусков сброшен"
    exit 0
fi

# Запуск основной логики
main
