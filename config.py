import os
from dotenv import load_dotenv

load_dotenv()

# Конфигурация бота
BOT_TOKEN = os.getenv('BOT_TOKEN')
USER_ID = int(os.getenv('USER_ID', 0))

# Настройки времени (МСК)
TIMEZONE = 'Europe/Moscow'

# Расписание задач
TASKS_SCHEDULE = {
    'meditation': {
        'time': '06:05',
        'check_time': '06:50',
        'days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'],
        'name': 'Медитация'
    },
    'planning': {
        'time': '09:00',
        'check_time': '09:16',
        'days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'],
        'name': 'Планирование'
    },
    'workout': {
        'time': '15:00',
        'check_time': '17:00',
        'days': ['monday', 'thursday', 'sunday'],
        'name': 'Тренировка'
    },
    'yoga': {
        'time': '15:00',
        'check_time': '17:00',
        'days': ['tuesday', 'wednesday', 'friday', 'saturday'],
        'name': 'Йога'
    }
}
