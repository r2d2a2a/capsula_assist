#!/usr/bin/env python3
"""
Скрипт для тестирования конфигурации бота
"""

import os
import sys
from pathlib import Path

def test_environment():
    """Тестирование переменных окружения"""
    print("🔍 Проверка переменных окружения...")
    
    # Проверяем наличие .env файла
    env_file = Path('.env')
    if not env_file.exists():
        print("❌ Файл .env не найден!")
        print("📝 Создайте файл .env с токеном бота и User ID")
        return False
    
    # Загружаем переменные
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("❌ Модуль python-dotenv не установлен!")
        print("📦 Установите: pip install python-dotenv")
        return False
    
    # Проверяем токен бота
    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token or bot_token == 'your_telegram_bot_token_here':
        print("❌ BOT_TOKEN не установлен или имеет значение по умолчанию!")
        return False
    
    # Проверяем User ID
    user_id = os.getenv('USER_ID')
    if not user_id or user_id == 'your_telegram_user_id_here':
        print("❌ USER_ID не установлен или имеет значение по умолчанию!")
        return False
    
    try:
        user_id_int = int(user_id)
        if user_id_int <= 0:
            print("❌ USER_ID должен быть положительным числом!")
            return False
    except ValueError:
        print("❌ USER_ID должен быть числом!")
        return False
    
    print("✅ Переменные окружения настроены корректно")
    return True

def test_dependencies():
    """Тестирование зависимостей"""
    print("\n🔍 Проверка зависимостей...")
    
    required_modules = [
        ('telegram', 'python-telegram-bot'),
        ('apscheduler', 'apscheduler'),
        ('pytz', 'pytz'),
        ('dotenv', 'python-dotenv')
    ]
    
    all_ok = True
    for module_name, package_name in required_modules:
        try:
            __import__(module_name)
            print(f"✅ {package_name}")
        except ImportError:
            print(f"❌ {package_name} не установлен!")
            all_ok = False
    
    return all_ok

def test_config():
    """Тестирование конфигурации"""
    print("\n🔍 Проверка конфигурации...")
    
    try:
        from config import TASKS_SCHEDULE, TIMEZONE
        
        print(f"✅ Часовой пояс: {TIMEZONE}")
        print(f"✅ Настроено задач: {len(TASKS_SCHEDULE)}")
        
        for task_name, task_config in TASKS_SCHEDULE.items():
            print(f"  📋 {task_config['name']}: {task_config['time']} ({len(task_config['days'])} дней)")
        
        return True
    except ImportError as e:
        print(f"❌ Ошибка импорта конфигурации: {e}")
        return False

def test_database():
    """Тестирование базы данных"""
    print("\n🔍 Проверка базы данных...")
    
    try:
        from database import TaskDatabase
        
        # Создаем тестовую базу данных
        test_db = TaskDatabase('test_tasks.db')
        
        # Тестируем основные операции
        task_id = test_db.add_task('test_task', '2024-01-01')
        test_db.mark_task_completed('test_task', '2024-01-01', True)
        stats = test_db.get_completion_stats('2024-01-01', '2024-01-01')
        
        # Удаляем тестовую базу
        import os
        if os.path.exists('test_tasks.db'):
            os.remove('test_tasks.db')
        
        print("✅ База данных работает корректно")
        return True
    except Exception as e:
        print(f"❌ Ошибка базы данных: {e}")
        return False

def main():
    """Основная функция тестирования"""
    print("🤖 Тестирование конфигурации бота-ассистента\n")
    
    tests = [
        test_environment,
        test_dependencies,
        test_config,
        test_database
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Ошибка при выполнении теста: {e}")
            results.append(False)
    
    print("\n" + "="*50)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
    print("="*50)
    
    test_names = [
        "Переменные окружения",
        "Зависимости",
        "Конфигурация",
        "База данных"
    ]
    
    all_passed = True
    for i, (name, result) in enumerate(zip(test_names, results)):
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        print(f"{i+1}. {name}: {status}")
        if not result:
            all_passed = False
    
    print("="*50)
    if all_passed:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! Бот готов к запуску.")
        print("\n🚀 Для запуска выполните: python bot.py")
    else:
        print("⚠️  НЕКОТОРЫЕ ТЕСТЫ ПРОВАЛЕНЫ!")
        print("📖 См. инструкции в SETUP.md")
    
    return all_passed

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
