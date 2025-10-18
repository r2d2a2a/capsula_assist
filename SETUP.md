# Инструкция по настройке бота

## Шаг 1: Создание Telegram бота

1. Откройте Telegram и найдите бота **@BotFather**
2. Отправьте команду `/newbot`
3. Введите имя для вашего бота (например: "Мой Ассистент Задач")
4. Введите username для бота (например: "my_task_assistant_bot")
5. **Сохраните токен**, который выдаст BotFather (выглядит как: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

## Шаг 2: Получение User ID

1. Найдите бота **@userinfobot** в Telegram
2. Отправьте ему любое сообщение
3. **Сохраните ваш User ID** (число, например: `123456789`)

## Шаг 3: Установка зависимостей

Откройте терминал в папке с проектом и выполните:

```bash
pip install -r requirements.txt
```

## Шаг 4: Создание файла .env

Создайте файл `.env` в корне проекта со следующим содержимым:

```
BOT_TOKEN=ваш_токен_от_BotFather
USER_ID=ваш_user_id
```

**Пример:**
```
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
USER_ID=123456789
```

## Шаг 5: Запуск бота

Выполните одну из команд:

```bash
python bot.py
```

или

```bash
python run.py
```

## Шаг 6: Первый запуск

1. Найдите вашего бота в Telegram по username
2. Отправьте команду `/start`
3. Если все настроено правильно, бот ответит приветственным сообщением

## Проверка работы

Отправьте боту команду `/help` - вы должны увидеть список доступных команд.

## Автозапуск (опционально)

### На macOS (с помощью launchd):

1. Создайте файл `~/Library/LaunchAgents/com.taskassistant.bot.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.taskassistant.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/artursamsutdinov/Desktop/Капсула Ассистент/bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/artursamsutdinov/Desktop/Капсула Ассистент</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

2. Загрузите задачу:
```bash
launchctl load ~/Library/LaunchAgents/com.taskassistant.bot.plist
```

### На Linux (с помощью systemd):

1. Создайте файл `/etc/systemd/system/taskassistant.service`:

```ini
[Unit]
Description=Task Assistant Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/your/bot
ExecStart=/usr/bin/python3 /path/to/your/bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

2. Включите и запустите сервис:
```bash
sudo systemctl enable taskassistant.service
sudo systemctl start taskassistant.service
```

## Устранение неполадок

### Бот не отвечает
- Проверьте правильность токена в `.env`
- Убедитесь, что User ID указан корректно
- Проверьте логи в консоли

### Ошибки при установке зависимостей
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Проблемы с правами доступа
```bash
chmod +x run.py
```

## Безопасность

- **Никогда не делитесь** вашим BOT_TOKEN
- **Не публикуйте** файл `.env` в публичных репозиториях
- Регулярно проверяйте логи на предмет подозрительной активности

## Поддержка

При возникновении проблем:
1. Проверьте логи в консоли
2. Убедитесь в правильности всех настроек
3. Перезапустите бота
4. Проверьте подключение к интернету
