"""
Утилиты для работы с ботом
"""

import datetime
import pytz
from typing import Dict, List

def get_moscow_time() -> datetime.datetime:
    """Получить текущее время в московском часовом поясе"""
    return datetime.datetime.now(pytz.timezone('Europe/Moscow'))

def format_time(time_obj: datetime.datetime) -> str:
    """Форматировать время для отображения"""
    return time_obj.strftime('%H:%M')

def format_date(date_obj: datetime.datetime) -> str:
    """Форматировать дату для отображения"""
    return date_obj.strftime('%Y-%m-%d')

def get_week_start_end(date: datetime.datetime) -> tuple:
    """Получить начало и конец недели для указанной даты"""
    week_start = date - datetime.timedelta(days=date.weekday())
    week_end = week_start + datetime.timedelta(days=6)
    return week_start, week_end

def get_day_name(day_number: int) -> str:
    """Получить название дня недели по номеру (0=понедельник)"""
    days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
    return days[day_number]

def calculate_completion_rate(completed: int, total: int) -> float:
    """Вычислить процент выполнения"""
    if total == 0:
        return 0.0
    return round((completed / total) * 100, 1)

def get_task_emoji(task_type: str) -> str:
    """Получить эмодзи для типа задачи"""
    emojis = {
        'meditation': '🧘',
        'planning': '📝',
        'workout': '💪',
        'yoga': '🧘‍♀️'
    }
    return emojis.get(task_type, '📋')

def format_task_status(completed: bool) -> str:
    """Форматировать статус задачи"""
    return "✅ Выполнено" if completed else "❌ Не выполнено"

def create_progress_bar(completed: int, total: int, length: int = 10) -> str:
    """Создать текстовую полосу прогресса"""
    if total == 0:
        return "━" * length
    
    filled = int((completed / total) * length)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {completed}/{total}"

def get_motivational_message(completion_rate: float) -> str:
    """Получить мотивационное сообщение на основе процента выполнения"""
    if completion_rate >= 90:
        return "🎉 Отличная работа! Вы на высоте!"
    elif completion_rate >= 70:
        return "👍 Хорошая работа! Продолжайте в том же духе!"
    elif completion_rate >= 50:
        return "💪 Неплохо! Есть к чему стремиться!"
    elif completion_rate >= 30:
        return "📈 Есть прогресс! Не сдавайтесь!"
    else:
        return "🌟 Каждый день - новая возможность! Вы справитесь!"

def validate_time_format(time_str: str) -> bool:
    """Проверить корректность формата времени HH:MM"""
    try:
        datetime.datetime.strptime(time_str, '%H:%M')
        return True
    except ValueError:
        return False

def parse_time(time_str: str) -> tuple:
    """Разобрать строку времени на часы и минуты"""
    try:
        time_obj = datetime.datetime.strptime(time_str, '%H:%M')
        return time_obj.hour, time_obj.minute
    except ValueError:
        raise ValueError(f"Неверный формат времени: {time_str}. Ожидается HH:MM")

def get_next_occurrence(day_name: str, time_str: str) -> datetime.datetime:
    """Получить следующее вхождение задачи по дню недели и времени"""
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.datetime.now(moscow_tz)
    
    # Маппинг дней недели
    day_mapping = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }
    
    target_day = day_mapping.get(day_name.lower())
    if target_day is None:
        raise ValueError(f"Неверное название дня: {day_name}")
    
    hour, minute = parse_time(time_str)
    
    # Вычисляем дни до следующего вхождения
    days_ahead = target_day - now.weekday()
    if days_ahead <= 0:  # Если день уже прошел на этой неделе
        days_ahead += 7
    
    next_date = now + datetime.timedelta(days=days_ahead)
    next_occurrence = next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    return next_occurrence
