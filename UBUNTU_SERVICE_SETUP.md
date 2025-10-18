# Установка Capsula Assistant как системного сервиса на Ubuntu

Это руководство поможет вам настроить Telegram бота Capsula Assistant как системный сервис на Ubuntu, который будет автоматически запускаться при загрузке системы и перезапускаться при сбоях.

## 📋 Требования

- Ubuntu 18.04+ (или другая система с systemd)
- Python 3.8+
- Права root для установки сервиса
- Бот должен быть размещен в `/Capsula_assist/capsula_assist/`
- Виртуальное окружение в `/Capsula_assist/capsula_assist/venv/`

## 🚀 Быстрая установка

### 1. Подготовка файлов

Убедитесь, что у вас есть все необходимые файлы в директории проекта:

```bash
# Скопируйте файлы сервиса в директорию проекта
cp capsula-assistant.service /Capsula_assist/capsula_assist/
cp install_service.sh /Capsula_assist/capsula_assist/
cp manage_service.sh /Capsula_assist/capsula_assist/
cp monitor_service.sh /Capsula_assist/capsula_assist/
```

### 2. Установка сервиса

```bash
# Перейдите в директорию проекта
cd /Capsula_assist/capsula_assist/

# Сделайте скрипты исполняемыми
chmod +x install_service.sh manage_service.sh monitor_service.sh

# Запустите установку от имени root
sudo ./install_service.sh
```

### 3. Проверка установки

```bash
# Проверьте статус сервиса
sudo systemctl status capsula-assistant

# Посмотрите логи
sudo journalctl -u capsula-assistant -f
```

## 🛠️ Управление сервисом

Используйте скрипт `manage_service.sh` для удобного управления:

```bash
# Запустить сервис
./manage_service.sh start

# Остановить сервис
./manage_service.sh stop

# Перезапустить сервис
./manage_service.sh restart

# Показать статус
./manage_service.sh status

# Показать логи
./manage_service.sh logs

# Показать логи в реальном времени
./manage_service.sh logs-f

# Включить автозапуск
./manage_service.sh enable

# Отключить автозапуск
./manage_service.sh disable

# Удалить сервис
./manage_service.sh uninstall
```

## 📊 Мониторинг и логирование

### Автоматический мониторинг

Настройте автоматический мониторинг сервиса:

```bash
# Добавьте в crontab для проверки каждые 5 минут
sudo crontab -e

# Добавьте строку:
*/5 * * * * /Capsula_assist/capsula_assist/monitor_service.sh
```

### Просмотр логов

```bash
# Логи systemd (основные)
sudo journalctl -u capsula-assistant

# Логи в реальном времени
sudo journalctl -u capsula-assistant -f

# Логи мониторинга
./monitor_service.sh --logs

# Статус мониторинга
./monitor_service.sh --status
```

### Настройка ротации логов

```bash
# Установите конфигурацию logrotate
sudo cp logrotate_config /etc/logrotate.d/capsula-assistant

# Создайте директорию для логов
sudo mkdir -p /var/log/capsula-assistant
sudo chown root:root /var/log/capsula-assistant
```

## ⚙️ Конфигурация сервиса

### Основные настройки

Сервис настроен со следующими параметрами:

- **Автоперезапуск**: При сбоях сервис автоматически перезапускается через 10 секунд
- **Ограничения ресурсов**: Максимум 512MB RAM, 50% CPU
- **Безопасность**: Запуск в изолированной среде
- **Логирование**: Все логи сохраняются в systemd journal

### Изменение настроек

Для изменения настроек отредактируйте файл сервиса:

```bash
sudo nano /etc/systemd/system/capsula-assistant.service
```

После изменений перезагрузите конфигурацию:

```bash
sudo systemctl daemon-reload
sudo systemctl restart capsula-assistant
```

## 🔧 Устранение неполадок

### Сервис не запускается

1. Проверьте логи:
   ```bash
   sudo journalctl -u capsula-assistant --no-pager
   ```

2. Проверьте права доступа:
   ```bash
   ls -la /Capsula_assist/capsula_assist/
   ls -la /Capsula_assist/capsula_assist/venv/bin/python
   ```

3. Проверьте файл .env:
   ```bash
   cat /Capsula_assist/capsula_assist/.env
   ```

### Проблемы с виртуальным окружением

```bash
# Проверьте виртуальное окружение
/Capsula_assist/capsula_assist/venv/bin/python --version

# Переустановите зависимости
/Capsula_assist/capsula_assist/venv/bin/pip install -r requirements.txt
```

### Проблемы с правами доступа

```bash
# Установите правильные права
sudo chown -R root:root /Capsula_assist/capsula_assist/
sudo chmod +x /Capsula_assist/capsula_assist/run.py
```

## 📝 Полезные команды

```bash
# Показать все сервисы
systemctl list-units --type=service

# Показать автозапускаемые сервисы
systemctl list-unit-files --type=service --state=enabled

# Перезагрузить systemd
sudo systemctl daemon-reload

# Показать информацию о сервисе
systemctl show capsula-assistant

# Показать зависимости сервиса
systemctl list-dependencies capsula-assistant
```

## 🔄 Обновление бота

При обновлении кода бота:

```bash
# Остановите сервис
sudo systemctl stop capsula-assistant

# Обновите код
# ... ваши изменения ...

# Запустите сервис
sudo systemctl start capsula-assistant
```

## 🗑️ Удаление сервиса

```bash
# Используйте скрипт управления
./manage_service.sh uninstall

# Или вручную
sudo systemctl stop capsula-assistant
sudo systemctl disable capsula-assistant
sudo rm /etc/systemd/system/capsula-assistant.service
sudo systemctl daemon-reload
```

## 📞 Поддержка

При возникновении проблем:

1. Проверьте логи сервиса
2. Убедитесь в правильности путей
3. Проверьте наличие файла .env
4. Убедитесь в работоспособности виртуального окружения

---

**Примечание**: Этот сервис настроен для запуска от имени root. В продакшене рекомендуется создать отдельного пользователя для запуска бота.
