#!/bin/bash

# Скрипт для перезапуска сервиса бота с новыми настройками
# Исправляет проблемы с часовым поясом и дублированием уведомлений

echo "🔄 Перезапуск сервиса Capsula Assistant..."

# Останавливаем сервис
echo "⏹️ Остановка сервиса..."
sudo systemctl stop capsula-assistant

# Ждем полной остановки
sleep 3

# Проверяем, что сервис остановлен
if systemctl is-active --quiet capsula-assistant; then
    echo "⚠️ Сервис все еще работает, принудительная остановка..."
    sudo systemctl kill capsula-assistant
    sleep 2
fi

# Запускаем сервис
echo "🚀 Запуск сервиса с новыми настройками..."
sudo systemctl start capsula-assistant

# Ждем запуска
sleep 3

# Проверяем статус
if systemctl is-active --quiet capsula-assistant; then
    echo "✅ Сервис успешно запущен!"
    echo "📊 Статус сервиса:"
    sudo systemctl status capsula-assistant --no-pager -l
else
    echo "❌ Ошибка при запуске сервиса!"
    echo "📋 Логи сервиса:"
    sudo journalctl -u capsula-assistant --no-pager -l -n 20
    exit 1
fi

echo ""
echo "🎯 Исправления применены:"
echo "   • Часовой пояс принудительно установлен на Europe/Moscow"
echo "   • Время проверки йоги изменено с 17:00 на 17:15"
echo "   • Добавлена защита от дублирования уведомлений"
echo "   • Улучшены ID задач в планировщике"
echo ""
echo "💡 Теперь бот будет использовать московское время для всех операций!"
