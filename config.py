import os
from dotenv import load_dotenv

load_dotenv()

# Конфигурация бота
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Часовой пояс по умолчанию (для новых пользователей и обратной совместимости)
DEFAULT_TIMEZONE = os.getenv('DEFAULT_TIMEZONE', 'Europe/Moscow')

# Расписание задач
TASKS_SCHEDULE = {
    'meditation': {
        'time': '06:05',
        'check_time': '06:50',
        'days': [0, 1, 2, 3, 4, 5, 6],  # 0=понедельник, 6=воскресенье
        'name': 'Медитация'
    },
    'planning': {
        'time': '09:00',
        'check_time': '09:16',
        'days': [0, 1, 2, 3, 4, 5, 6],  # 0=понедельник, 6=воскресенье
        'name': 'Планирование'
    },
    'workout': {
        'time': '15:00',
        'check_time': '17:00',
        'days': [0, 3, 6],  # понедельник, четверг, воскресенье
        'name': 'Тренировка'
    },
    'yoga': {
        'time': '15:00',
        'check_time': '17:15',  # Изменено с 17:00 на 17:15 для избежания дублирования
        'days': [1, 2, 4, 5],  # вторник, среда, пятница, суббота
        'name': 'Йога'
    }
}
