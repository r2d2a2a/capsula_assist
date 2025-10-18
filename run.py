#!/usr/bin/env python3
"""
Скрипт для запуска телеграм бота-ассистента
"""

import sys
import os
from pathlib import Path

# Добавляем текущую директорию в путь Python
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

def check_env_file():
    """Проверяем наличие файла .env"""
    env_file = current_dir / '.env'
    if not env_file.exists():
        print("❌ Файл .env не найден!")
        print("📝 Создайте файл .env со следующим содержимым:")
        print("BOT_TOKEN=your_telegram_bot_token_here")
        print("USER_ID=your_telegram_user_id_here")
        print("\n🔧 Инструкции по получению токена и ID см. в README.md")
        return False
    return True

def main():
    """Основная функция запуска"""
    print("🤖 Запуск Телеграм Бота-Ассистента...")
    
    # Проверяем наличие .env файла
    if not check_env_file():
        return
    
    # Проверяем наличие необходимых модулей
    try:
        import telegram
        import apscheduler
        import pytz
        import dotenv
    except ImportError as e:
        print(f"❌ Отсутствует необходимый модуль: {e}")
        print("📦 Установите зависимости: pip install -r requirements.txt")
        return
    
    # Запускаем бота
    try:
        from bot import main as bot_main
        import asyncio
        
        # Простое решение: всегда создаем новый event loop
        print("🚀 Запуск бота...")
        asyncio.run(bot_main())
            
    except KeyboardInterrupt:
        print("\n⏹️ Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
