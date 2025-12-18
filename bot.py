import logging
import datetime
from typing import Dict, List, Optional
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from config import BOT_TOKEN, DEFAULT_TIMEZONE, TASKS_SCHEDULE
from database import TaskDatabase
import utils

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# Убираем шум от httpx/httpcore (polling getUpdates)
for noisy_logger in ["httpx", "httpcore"]:
    try:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    except Exception:
        pass

class TaskAssistantBot:
    def __init__(self):
        self.db = TaskDatabase()
        # Планировщик держим в UTC, а timezone задаем на уровне CronTrigger для каждого пользователя.
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
        self.add_task_state: Dict[int, Dict] = {}
        self.edit_task_state: Dict[int, Dict] = {}
        self.setup_scheduler()

    def _tzinfo_from_string(self, tz_str: str):
        """Преобразовать строку timezone в tzinfo.

        Поддерживаем:
        - IANA timezone (например, Europe/Moscow)
        - фиксированный оффсет: offset:+180 (минуты)
        """
        tz_str = (tz_str or '').strip()
        if tz_str.startswith('offset:'):
            try:
                minutes = int(tz_str.split(':', 1)[1])
                return pytz.FixedOffset(minutes)
            except Exception:
                return pytz.timezone(DEFAULT_TIMEZONE)
        try:
            return pytz.timezone(tz_str or DEFAULT_TIMEZONE)
        except Exception:
            return pytz.timezone(DEFAULT_TIMEZONE)

    def _format_timezone(self, tz_str: str) -> str:
        tz_str = (tz_str or '').strip()
        if tz_str.startswith('offset:'):
            try:
                minutes = int(tz_str.split(':', 1)[1])
                sign = '+' if minutes >= 0 else '-'
                minutes_abs = abs(minutes)
                hh = minutes_abs // 60
                mm = minutes_abs % 60
                return f"UTC{sign}{hh:02d}:{mm:02d}"
            except Exception:
                return DEFAULT_TIMEZONE
        return tz_str or DEFAULT_TIMEZONE

    def _parse_timezone_input(self, text: str) -> Optional[str]:
        """Распарсить ввод пользователя в timezone-строку для хранения.

        Принимаем:
        - IANA timezone: Europe/Moscow, America/New_York
        - UTC / UTC+3 / UTC+03:00 / +3 / -5 / +03:30
        """
        raw = (text or '').strip()
        if not raw:
            return None
        upper = raw.upper()
        if upper == 'UTC':
            return 'offset:0'

        # Нормализуем ввод типа "+3", "UTC+3", "UTC+03:00"
        import re
        m = re.fullmatch(r'(?:UTC)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?\s*', upper)
        if m:
            sign, hh_s, mm_s = m.group(1), m.group(2), m.group(3)
            hh = int(hh_s)
            mm = int(mm_s) if mm_s is not None else 0
            if hh > 14 or mm >= 60:
                return None
            total = hh * 60 + mm
            if sign == '-':
                total = -total
            return f'offset:{total}'

        # Пробуем IANA timezone
        try:
            _ = pytz.timezone(raw)
            return raw
        except Exception:
            return None

    def _make_local_datetime(self, date_str: str, time_str: str, tz) -> Optional[datetime.datetime]:
        """Собрать локальный datetime из строки даты/времени в часовом поясе пользователя."""
        try:
            hour, minute = map(int, time_str.split(':'))
            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            return tz.localize(datetime.datetime(date_obj.year, date_obj.month, date_obj.day, hour, minute, 0))
        except Exception as e:
            logger.error(f"Не удалось собрать дату {date_str} {time_str}: {e}")
            return None

    def _get_next_occurrence_for_def(self, task_def: Dict, tz) -> Optional[datetime.datetime]:
        """Вычислить ближайшее время напоминания для задачи с учетом периодичности."""
        now = datetime.datetime.now(tz)
        reminder_time = task_def.get('reminder_time')
        if not reminder_time:
            return None
        freq = task_def.get('frequency')
        if freq == 'once':
            date_str = task_def.get('one_time_date')
            if not date_str:
                return None
            dt = self._make_local_datetime(date_str, reminder_time, tz)
            if dt and dt >= now:
                return dt
            return None
        days_list = task_def.get('days_list') or list(range(7))
        for offset in range(0, 8):
            candidate = now + datetime.timedelta(days=offset)
            if candidate.weekday() in days_list:
                candidate_dt = candidate.replace(hour=int(reminder_time.split(':')[0]), minute=int(reminder_time.split(':')[1]),
                                                 second=0, microsecond=0)
                if candidate_dt >= now:
                    return candidate_dt
        return None

    def _build_calendar_link_for_def(self, task_def: Dict, tz) -> Optional[str]:
        """Сформировать ссылку для добавления ближайшего события в Google Calendar."""
        start_dt = self._get_next_occurrence_for_def(task_def, tz)
        if not start_dt:
            return None
        check_time = task_def.get('check_time')
        end_dt = None
        if check_time:
            end_candidate = self._make_local_datetime(start_dt.strftime('%Y-%m-%d'), check_time, tz)
            if end_candidate and end_candidate > start_dt:
                end_dt = end_candidate
        if not end_dt:
            end_dt = start_dt + datetime.timedelta(minutes=30)
        tz_name = getattr(tz, 'zone', None) or getattr(tz, 'key', None) or DEFAULT_TIMEZONE
        return utils.build_google_calendar_link(task_def.get('name', 'Task'), start_dt, end_dt, tz_name, "Создано через капсулу ассистента")

    def _build_reminder_keyboard(self, def_id: int, date_str: str) -> InlineKeyboardMarkup:
        """Клавиатура напоминания с кнопкой Snooze."""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⏰ Напомнить через 30 мин", callback_data=f"v2_snooze_{def_id}_{date_str}_30")],
            [InlineKeyboardButton("❌ Пропустить напоминание", callback_data=f"v2_skip_reminder_{def_id}_{date_str}")]
        ])
    
    def setup_scheduler(self):
        """Базовая инициализация планировщика (планирования добавляются по пользователям)."""
        logger.info(f"APScheduler timezone: {self.scheduler.timezone}")
        for job in self.scheduler.get_jobs():
            try:
                next_run = getattr(job, 'next_run_time', None)
            except Exception:
                next_run = None
            logger.info(f"Job {job.id} next run: {next_run}")

    def schedule_reports_for_user(self, chat_id: int, user_id: int):
        tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
        daily_id = f'daily_report_{chat_id}'
        weekly_id = f'weekly_report_{chat_id}'
        self.scheduler.add_job(
            self.send_daily_report_v2,
            CronTrigger(hour=20, minute=0, timezone=tz),
            args=[chat_id, user_id],
            id=daily_id,
            replace_existing=True
        )
        self.scheduler.add_job(
            self.send_weekly_report_v2,
            CronTrigger(day_of_week=6, hour=20, minute=30, timezone=tz),
            args=[chat_id, user_id],
            id=weekly_id,
            replace_existing=True
        )

    def schedule_task_definition(self, chat_id: int, user_id: int, task_def: Dict):
        tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
        def_id = task_def['id']
        name = task_def['name']
        freq = task_def.get('frequency')
        # Одноразовая задача: планируем точные даты через DateTrigger
        if freq == 'once':
            date_str = task_def.get('one_time_date')
            reminder_dt = self._make_local_datetime(date_str, task_def['reminder_time'], tz) if date_str else None
            check_dt = self._make_local_datetime(date_str, task_def['check_time'], tz) if date_str else None
            now = datetime.datetime.now(tz)
            if reminder_dt and reminder_dt > now:
                r_job_id = f'v2_reminder_{chat_id}_{def_id}_once'
                self.scheduler.add_job(
                    self.send_task_reminder_v2,
                    DateTrigger(run_date=reminder_dt.astimezone(pytz.UTC)),
                    args=[chat_id, user_id, def_id, name],
                    id=r_job_id,
                    replace_existing=True
                )
            if check_dt and check_dt > now:
                c_job_id = f'v2_check_{chat_id}_{def_id}_once'
                self.scheduler.add_job(
                    self.send_completion_check_v2,
                    DateTrigger(run_date=check_dt.astimezone(pytz.UTC)),
                    args=[chat_id, user_id, def_id, name],
                    id=c_job_id,
                    replace_existing=True
                )
            return

        days: List[int] = task_def.get('days_list') or list(range(7))
        rh, rm = map(int, task_def['reminder_time'].split(':'))
        ch, cm = map(int, task_def['check_time'].split(':'))
        for day in days:
            r_job_id = f'v2_reminder_{chat_id}_{def_id}_{day}_{rh:02d}{rm:02d}'
            c_job_id = f'v2_check_{chat_id}_{def_id}_{day}_{ch:02d}{cm:02d}'
            self.scheduler.add_job(
                self.send_task_reminder_v2,
                CronTrigger(day_of_week=day, hour=rh, minute=rm, timezone=tz),
                args=[chat_id, user_id, def_id, name],
                id=r_job_id,
                replace_existing=True
            )
            self.scheduler.add_job(
                self.send_completion_check_v2,
                CronTrigger(day_of_week=day, hour=ch, minute=cm, timezone=tz),
                args=[chat_id, user_id, def_id, name],
                id=c_job_id,
                replace_existing=True
            )

    def unschedule_task_definition(self, chat_id: int, def_id: int):
        """Удалить все задания напоминаний/проверок для указанного определения задачи."""
        try:
            for job in list(self.scheduler.get_jobs()):
                jid = getattr(job, 'id', '')
                if isinstance(jid, str) and (jid.startswith(f'v2_reminder_{chat_id}_{def_id}_') or jid.startswith(f'v2_check_{chat_id}_{def_id}_')):
                    try:
                        self.scheduler.remove_job(jid)
                    except Exception:
                        pass
                if isinstance(jid, str) and jid.startswith(f'snooze_{chat_id}_{def_id}_'):
                    try:
                        self.scheduler.remove_job(jid)
                    except Exception:
                        pass
        except Exception:
            pass

    def catch_up_missed_for_user(self, chat_id: int, user_id: int):
        """Догнать пропущенные напоминания/контроль для текущего дня после простоя."""
        tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
        now = datetime.datetime.now(tz)
        today_str = now.strftime('%Y-%m-%d')
        weekday = now.weekday()
        defs = self.db.list_task_definitions(user_id)
        for d in defs:
            freq = d.get('frequency')
            if freq == 'once':
                date_str = d.get('one_time_date')
                if date_str != today_str:
                    continue
            else:
                days_list = d.get('days_list') or list(range(7))
                if weekday not in days_list:
                    continue
                date_str = today_str

            # Напоминание
            reminder_dt = self._make_local_datetime(date_str, d.get('reminder_time'), tz)
            if reminder_dt and reminder_dt <= now:
                lock_acquired, _ = self.db.acquire_send_lock_v2(user_id, d['id'], date_str)
                if lock_acquired:
                    run_time = datetime.datetime.now(pytz.UTC) + datetime.timedelta(seconds=1)
                    job_id = f'catchup_reminder_{chat_id}_{d["id"]}_{date_str}'
                    self.scheduler.add_job(
                        self.send_task_reminder_v2,
                        DateTrigger(run_date=run_time),
                        args=[chat_id, user_id, d['id'], d.get('name'), True, False],
                        id=job_id,
                        replace_existing=True
                    )

            # Контроль выполнения
            check_dt = self._make_local_datetime(date_str, d.get('check_time'), tz)
            if check_dt and check_dt <= now:
                lock_acquired, _ = self.db.acquire_check_lock_v2(user_id, d['id'], date_str)
                if lock_acquired:
                    run_time = datetime.datetime.now(pytz.UTC) + datetime.timedelta(seconds=2)
                    job_id = f'catchup_check_{chat_id}_{d["id"]}_{date_str}'
                    self.scheduler.add_job(
                        self.send_completion_check_v2,
                        DateTrigger(run_date=run_time),
                        args=[chat_id, user_id, d['id'], d.get('name'), True],
                        id=job_id,
                        replace_existing=True
                    )

    def schedule_snoozed_reminder(self, chat_id: int, user_id: int, task_def_id: int, task_name: str,
                                  delay_minutes: int):
        """Запланировать одноразовое напоминание после Snooze."""
        tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
        run_time_local = datetime.datetime.now(tz) + datetime.timedelta(minutes=delay_minutes)
        run_time_utc = run_time_local.astimezone(pytz.UTC)
        job_id = f'snooze_{chat_id}_{task_def_id}_{int(run_time_utc.timestamp())}'
        self.scheduler.add_job(
            self.send_task_reminder_v2,
            DateTrigger(run_date=run_time_utc),
            args=[chat_id, user_id, task_def_id, task_name, False, True],
            id=job_id,
            replace_existing=False
        )

    def schedule_all_for_user(self, chat_id: int, user_id: int):
        defs = self.db.list_task_definitions(user_id)
        for d in defs:
            self.schedule_task_definition(chat_id, user_id, d)
        self.schedule_reports_for_user(chat_id, user_id)
        # После планирования догоняем пропущенные события текущего дня
        try:
            self.catch_up_missed_for_user(chat_id, user_id)
        except Exception as e:
            logger.error(f"catch_up_missed_for_user error: {e}")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        chat_id = update.effective_chat.id
        username = update.effective_user.username
        user_id = self.db.upsert_user(chat_id, username)
        tz_str = self.db.get_user_timezone(user_id)
        self.schedule_all_for_user(chat_id, user_id)
        
        welcome_text = """
🤖 Добро пожаловать в ваш персональный ассистент задач!

Добавьте свои задачи и получайте напоминания и контроль выполнения.

📊 Отчеты:
• Ежедневный отчет в 20:00
• Еженедельный отчет в воскресенье в 20:30

Используйте /help для получения списка команд.
        """
        await update.message.reply_text(welcome_text)
        await update.message.reply_text(
            f"Ваш часовой пояс сейчас: {self._format_timezone(tz_str)}\n"
            "Если вы не в МСК — задайте свой часовой пояс командой /timezone.\n\n"
            "Добавьте свою задачу командой /addtask. Посмотреть список: /mytasks"
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help"""
        help_text = """
📖 Доступные команды:

/start - Начать работу с ботом
/help - Показать это сообщение
/addtask - Добавить задачу (до 10)
/cancel - Отменить добавление задачи
/mytasks - Список моих задач
/today - Показать задачи на сегодня
/stats - Показать статистику за сегодня
/report - Получить отчет за сегодня
 /timezone - Установить часовой пояс
 /edittask - Редактировать задачу (список с кнопками)
 /deletetask - Удалить задачу (список с кнопками)
 
🔧 Управление:
/start_bot - Запустить напоминания
/stop_bot - Остановить напоминания
        """
        await update.message.reply_text(help_text)

    async def timezone_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /timezone — установка TZ пользователя."""
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            user_id = self.db.upsert_user(chat_id, update.effective_user.username)
        else:
            user_id = user['id']
        tz_str = self.db.get_user_timezone(user_id)

        keyboard = [
            [InlineKeyboardButton("Europe/Moscow", callback_data="tz_set_Europe/Moscow")],
            [InlineKeyboardButton("Europe/Berlin", callback_data="tz_set_Europe/Berlin")],
            [InlineKeyboardButton("America/New_York", callback_data="tz_set_America/New_York")],
            [InlineKeyboardButton("Asia/Dubai", callback_data="tz_set_Asia/Dubai")],
            [InlineKeyboardButton("UTC+03:00", callback_data="tz_set_offset:+180"),
             InlineKeyboardButton("UTC+05:00", callback_data="tz_set_offset:+300")],
            [InlineKeyboardButton("Ввести вручную", callback_data="tz_manual")],
        ]
        context.user_data['awaiting_timezone'] = True
        await update.message.reply_text(
            f"Текущий часовой пояс: {self._format_timezone(tz_str)}\n\n"
            "Выберите вариант кнопкой или отправьте сообщением, например:\n"
            "- Europe/Paris\n"
            "- America/Los_Angeles\n"
            "- UTC+03:00 или +3\n",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def send_task_reminder(self, task_type: str, task_name: str):
        """Отправить напоминание о задаче"""
        try:
            today = datetime.datetime.now(pytz.timezone(DEFAULT_TIMEZONE)).strftime('%Y-%m-%d')
            # Атомарно получаем право на отправку, чтобы избежать дублей
            lock_acquired, _ = self.db.acquire_send_lock(task_type, today)
            if not lock_acquired:
                logger.info(f"Пропускаем дубликат напоминания для {task_type} на {today}")
                return
            # Отправляем напоминание
            message = f"⏰ Напоминание!\n\n📋 Время для: {task_name}\n🕐 {datetime.datetime.now(pytz.timezone(DEFAULT_TIMEZONE)).strftime('%H:%M')}"
            # Напоминание отправляем без кнопок. Кнопки показываются только при контроле выполнения.
            await self.send_message_to_user(message, reply_markup=None)
            
            # Флаг already set в acquire_send_lock
            
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания: {e}")
    
    async def send_task_reminder_v2(self, chat_id: int, user_id: int, task_def_id: int, task_name: str,
                                    catch_up: bool = False, snoozed: bool = False):
        """Многопользовательское напоминание."""
        try:
            tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
            now = datetime.datetime.now(tz)
            today = now.strftime('%Y-%m-%d')
            if not snoozed:
                lock_acquired, _ = self.db.acquire_send_lock_v2(user_id, task_def_id, today)
                if not lock_acquired:
                    return
            message = f"⏰ Напоминание!\n\n📋 Время для: {task_name}\n🕐 {now.strftime('%H:%M')}"
            if snoozed:
                message += "\n\n🔁 Повторное напоминание после Snooze."
            if catch_up:
                message += "\n\n⚠️ Отправлено после простоя сервера."
            await self.send_message_to_chat(chat_id, message, reply_markup=self._build_reminder_keyboard(task_def_id, today))
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания v2: {e}")
    
    async def send_completion_check(self, task_type: str, task_name: str):
        """Отправить проверку выполнения задачи"""
        try:
            today = datetime.datetime.now(pytz.timezone(DEFAULT_TIMEZONE)).strftime('%Y-%m-%d')
            
            # Атомарно получаем право на отправку проверки, чтобы избежать дублей
            lock_acquired, _ = self.db.acquire_check_lock(task_type, today)
            if not lock_acquired:
                logger.info(f"Пропускаем дубликат проверки для {task_type} на {today}")
                return
            
            message = f"🔍 Контроль выполнения!\n\n📋 Задача: {task_name}\n⏰ Время проверки: {datetime.datetime.now(pytz.timezone(DEFAULT_TIMEZONE)).strftime('%H:%M')}\n\nВыполнили ли вы эту задачу?"
            
            keyboard = [
                [InlineKeyboardButton("✅ Да, выполнил", callback_data=f"check_yes_{task_type}_{today}")],
                [InlineKeyboardButton("❌ Нет, не выполнил", callback_data=f"check_no_{task_type}_{today}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.send_message_to_user(message, reply_markup)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке проверки: {e}")
    
    async def send_completion_check_v2(self, chat_id: int, user_id: int, task_def_id: int, task_name: str, catch_up: bool = False):
        try:
            tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
            now = datetime.datetime.now(tz)
            today = now.strftime('%Y-%m-%d')
            lock_acquired, _ = self.db.acquire_check_lock_v2(user_id, task_def_id, today)
            if not lock_acquired:
                return
            message = f"🔍 Контроль выполнения!\n\n📋 Задача: {task_name}\n⏰ Время проверки: {now.strftime('%H:%M')}\n\nВыполнили ли вы эту задачу?"
            if catch_up:
                message += "\n\n⚠️ Проверка отправлена после простоя сервера."
            keyboard = [
                [InlineKeyboardButton("✅ Да, выполнил", callback_data=f"v2_check_yes_{task_def_id}_{today}")],
                [InlineKeyboardButton("❌ Нет, не выполнил", callback_data=f"v2_check_no_{task_def_id}_{today}")]
            ]
            await self.send_message_to_chat(chat_id, message, InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Ошибка при отправке проверки v2: {e}")
    
    async def send_daily_report(self):
        """Отправить ежедневный отчет"""
        try:
            today = datetime.datetime.now(pytz.timezone(DEFAULT_TIMEZONE)).strftime('%Y-%m-%d')
            stats = self.db.get_completion_stats(today, today)
            tasks = self.db.get_tasks_for_date(today)
            
            report = f"📊 Ежедневный отчет - {today}\n\n"
            report += f"📈 Общая статистика:\n"
            report += f"• Всего задач: {stats['total_tasks']}\n"
            report += f"• Выполнено: {stats['completed_tasks']}\n"
            report += f"• Процент выполнения: {stats['completion_rate']}%\n\n"
            
            report += "📋 Детали по задачам:\n"
            for task in tasks:
                status = "✅" if task['completed'] else "❌"
                report += f"• {task['task_type']}: {status}\n"
                if task.get('comment'):
                    report += f"   📝 {task['comment']}\n"
            
            # Сохраняем отчет
            self.db.save_report('daily', today, today, stats)
            
            await self.send_message_to_user(report)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке ежедневного отчета: {e}")
    
    async def send_daily_report_v2(self, chat_id: int, user_id: int):
        try:
            tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
            today = datetime.datetime.now(tz).strftime('%Y-%m-%d')
            stats = self.db.get_completion_stats_by_user(user_id, today, today)
            tasks = self.db.get_tasks_for_date_by_user(user_id, today)
            defs = {d['id']: d for d in self.db.list_task_definitions(user_id)}
            report = f"📊 Ежедневный отчет - {today}\n\n"
            report += f"📈 Общая статистика:\n"
            report += f"• Всего задач: {stats['total_tasks']}\n"
            report += f"• Выполнено: {stats['completed_tasks']}\n"
            report += f"• Процент выполнения: {stats['completion_rate']}%\n\n"
            report += "📋 Детали по задачам:\n"
            for task in tasks:
                status = "✅" if task.get('completed') else "❌"
                name = defs.get(task.get('task_def_id'), {}).get('name', f"#{task.get('task_def_id')}")
                report += f"• {name}: {status}\n"
                if task.get('comment'):
                    report += f"   📝 {task['comment']}\n"
            self.db.save_report('daily', today, today, stats, user_id)
            await self.send_message_to_chat(chat_id, report)
        except Exception as e:
            logger.error(f"Ошибка при отправке ежедневного отчета v2: {e}")
    
    async def send_weekly_report(self):
        """Отправить еженедельный отчет"""
        try:
            today = datetime.datetime.now(pytz.timezone(DEFAULT_TIMEZONE))
            week_start = (today - datetime.timedelta(days=today.weekday())).strftime('%Y-%m-%d')
            week_end = today.strftime('%Y-%m-%d')
            
            stats = self.db.get_completion_stats(week_start, week_end)
            tasks = self.db.get_tasks_for_period(week_start, week_end)
            
            report = f"📊 Еженедельный отчет\n"
            report += f"📅 Период: {week_start} - {week_end}\n\n"
            report += f"📈 Общая статистика:\n"
            report += f"• Всего задач: {stats['total_tasks']}\n"
            report += f"• Выполнено: {stats['completed_tasks']}\n"
            report += f"• Процент выполнения: {stats['completion_rate']}%\n\n"
            
            # Статистика по дням
            daily_stats = {}
            for task in tasks:
                date = task['date']
                if date not in daily_stats:
                    daily_stats[date] = {'total': 0, 'completed': 0}
                daily_stats[date]['total'] += 1
                if task['completed']:
                    daily_stats[date]['completed'] += 1
            
            report += "📅 Статистика по дням:\n"
            for date in sorted(daily_stats.keys()):
                day_stats = daily_stats[date]
                rate = (day_stats['completed'] / day_stats['total'] * 100) if day_stats['total'] > 0 else 0
                report += f"• {date}: {day_stats['completed']}/{day_stats['total']} ({rate:.1f}%)\n"

            # Комментарии за неделю
            comments = [t for t in tasks if t.get('comment')]
            if comments:
                report += "\n📝 Комментарии:\n"
                for t in comments:
                    report += f"• {t['date']} {t['task_type']}: {t['comment']}\n"
            
            # Сохраняем отчет
            self.db.save_report('weekly', week_start, week_end, stats)
            
            await self.send_message_to_user(report)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке еженедельного отчета: {e}")
    
    async def send_weekly_report_v2(self, chat_id: int, user_id: int):
        try:
            tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
            today = datetime.datetime.now(tz)
            week_start = (today - datetime.timedelta(days=today.weekday())).strftime('%Y-%m-%d')
            week_end = today.strftime('%Y-%m-%d')
            stats = self.db.get_completion_stats_by_user(user_id, week_start, week_end)
            tasks = self.db.get_tasks_for_period_by_user(user_id, week_start, week_end)
            defs = {d['id']: d for d in self.db.list_task_definitions(user_id)}
            report = f"📊 Еженедельный отчет\n"
            report += f"📅 Период: {week_start} - {week_end}\n\n"
            report += f"📈 Общая статистика:\n"
            report += f"• Всего задач: {stats['total_tasks']}\n"
            report += f"• Выполнено: {stats['completed_tasks']}\n"
            report += f"• Процент выполнения: {stats['completion_rate']}%\n\n"
            daily_stats = {}
            for task in tasks:
                date = task['date']
                if date not in daily_stats:
                    daily_stats[date] = {'total': 0, 'completed': 0}
                daily_stats[date]['total'] += 1
                if task.get('completed'):
                    daily_stats[date]['completed'] += 1
            report += "📅 Статистика по дням:\n"
            for date in sorted(daily_stats.keys()):
                day_stats = daily_stats[date]
                rate = (day_stats['completed'] / day_stats['total'] * 100) if day_stats['total'] > 0 else 0
                report += f"• {date}: {day_stats['completed']}/{day_stats['total']} ({rate:.1f}%)\n"
            comments = [t for t in tasks if t.get('comment')]
            if comments:
                report += "\n📝 Комментарии:\n"
                for t in comments:
                    name = defs.get(t.get('task_def_id'), {}).get('name', f"#{t.get('task_def_id')}")
                    report += f"• {t['date']} {name}: {t['comment']}\n"
            self.db.save_report('weekly', week_start, week_end, stats, user_id)
            await self.send_message_to_chat(chat_id, report)
        except Exception as e:
            logger.error(f"Ошибка при отправке еженедельного отчета v2: {e}")
    
    async def send_message_to_user(self, message: str, reply_markup=None):
        """Отправить сообщение пользователю"""
        # Этот метод будет переопределен в main функции
        pass
    
    async def send_message_to_chat(self, chat_id: int, message: str, reply_markup=None):
        """Отправить сообщение в конкретный чат (используется для многопользовательского режима)."""
        # Этот метод будет переопределен в main функции
        pass

    def build_days_keyboard(self, selected_days: List[int]) -> InlineKeyboardMarkup:
        days_names = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
        chosen = set(selected_days or [])
        rows = []
        for i in range(0, 7, 2):
            row = []
            for d in [i, i+1] if i+1 < 7 else [i]:
                label = ("✅ " if d in chosen else "") + days_names[d]
                row.append(InlineKeyboardButton(label, callback_data=f"addtask_day_{d}"))
            rows.append(row)
        rows.append([
            InlineKeyboardButton("Готово", callback_data="addtask_days_done"),
            InlineKeyboardButton("Отмена", callback_data="addtask_cancel")
        ])
        return InlineKeyboardMarkup(rows)

    def build_days_keyboard_edit(self, selected_days: List[int]) -> InlineKeyboardMarkup:
        days_names = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
        chosen = set(selected_days or [])
        rows = []
        for i in range(0, 7, 2):
            row = []
            for d in [i, i+1] if i+1 < 7 else [i]:
                label = ("✅ " if d in chosen else "") + days_names[d]
                row.append(InlineKeyboardButton(label, callback_data=f"edittask_day_{d}"))
            rows.append(row)
        rows.append([
            InlineKeyboardButton("Готово", callback_data="edittask_days_done"),
            InlineKeyboardButton("Отмена", callback_data="edittask_cancel")
        ])
        return InlineKeyboardMarkup(rows)

    def build_edit_menu_keyboard(self) -> InlineKeyboardMarkup:
        """Клавиатура панели редактирования с кнопкой Сохранить."""
        kb = [
            [InlineKeyboardButton("Название", callback_data="edittask_field_name"), InlineKeyboardButton("Периодичность", callback_data="edittask_field_freq")],
            [InlineKeyboardButton("Дни", callback_data="edittask_field_days"), InlineKeyboardButton("Дата (одноразовая)", callback_data="edittask_field_date")],
            [InlineKeyboardButton("Время напоминания", callback_data="edittask_field_reminder")],
            [InlineKeyboardButton("Время контроля", callback_data="edittask_field_check")],
            [InlineKeyboardButton("Сохранить", callback_data="edittask_save"), InlineKeyboardButton("Отмена", callback_data="edittask_cancel")]
        ]
        return InlineKeyboardMarkup(kb)

    def build_tasks_list_keyboard(self, defs: List[Dict], action: str, page: int, page_size: int = 5) -> InlineKeyboardMarkup:
        """Унифицированная клавиатура списка задач с пагинацией для редактирования/удаления."""
        prefix = "editlist" if action == "edit" else "dellist"
        start = max(page, 0) * page_size
        end = start + page_size
        rows: List[List[InlineKeyboardButton]] = []
        for d in defs[start:end]:
            rows.append([InlineKeyboardButton(f"{d.get('name')} (#{d.get('id')})", callback_data=f"{prefix}_choose_{d.get('id')}_{page}")])
        total_pages = max(1, (len(defs) - 1) // page_size + 1)
        nav_row: List[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"{prefix}_page_{page-1}"))
        if end < len(defs):
            nav_row.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"{prefix}_page_{page+1}"))
        if nav_row:
            rows.append(nav_row)
        rows.append([InlineKeyboardButton("Отмена", callback_data=f"{prefix}_cancel")])
        return InlineKeyboardMarkup(rows)

    async def show_task_picker(self, chat_id: int, user_id: int, action: str, page: int = 0, query=None):
        """Отображает список задач с пагинацией для выбора действия (edit/delete)."""
        defs = self.db.list_task_definitions(user_id)
        if not defs:
            text = "У вас пока нет задач. Добавьте новую командой /addtask."
            if query:
                await query.edit_message_text(text)
            else:
                await self.send_message_to_chat(chat_id, text)
            return
        page_size = 5
        total_pages = max(1, (len(defs) - 1) // page_size + 1)
        safe_page = min(max(page, 0), total_pages - 1)
        action_text = "редактирования" if action == "edit" else "удаления"
        text = f"Выберите задачу для {action_text} (страница {safe_page + 1}/{total_pages}):"
        markup = self.build_tasks_list_keyboard(defs, action, safe_page, page_size=page_size)
        if query:
            await query.edit_message_text(text, reply_markup=markup)
        else:
            await self.send_message_to_chat(chat_id, text, markup)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data

        # ----- Timezone setup -----
        if data.startswith('tz_set_'):
            chat_id = update.effective_chat.id
            user = self.db.get_user_by_chat_id(chat_id)
            if not user:
                user_id = self.db.upsert_user(chat_id, update.effective_user.username)
            else:
                user_id = user['id']
            tz_value = data[len('tz_set_'):]

            # Поддерживаем tz_set_offset:+180 и tz_set_Europe/Moscow
            parsed = tz_value

            # Валидация
            if parsed.startswith('offset:'):
                try:
                    _ = int(parsed.split(':', 1)[1])
                except Exception:
                    await query.edit_message_text("Не удалось распознать оффсет.")
                    return
            else:
                try:
                    _ = pytz.timezone(parsed)
                except Exception:
                    await query.edit_message_text("Не удалось распознать timezone.")
                    return

            self.db.set_user_timezone(user_id, parsed)
            self.unschedule_all_for_chat(chat_id)
            self.schedule_all_for_user(chat_id, user_id)
            context.user_data.pop('awaiting_timezone', None)
            await query.edit_message_text(f"✅ Часовой пояс сохранен: {self._format_timezone(parsed)}. Расписание обновлено.")
            return

        if data == 'tz_manual':
            context.user_data['awaiting_timezone'] = True
            await query.edit_message_text("Ок. Отправьте часовой пояс сообщением (например, Europe/Paris или UTC+03:00 / +3).")
            return

        # ----- Пагинация списков задач для редактирования/удаления -----
        if data.startswith(('editlist_', 'dellist_')):
            chat_id = update.effective_chat.id
            user = self.db.get_user_by_chat_id(chat_id)
            if not user:
                await query.edit_message_text("Начните с /start")
                return
            user_id = user['id']
            parts = data.split('_')
            prefix = parts[0]
            if parts[1] == 'page':
                page = int(parts[2])
                action = 'edit' if prefix == 'editlist' else 'delete'
                await self.show_task_picker(chat_id, user_id, action, page=page, query=query)
                return
            if parts[1] == 'choose':
                def_id = int(parts[2])
                page = int(parts[3]) if len(parts) > 3 else 0
                if prefix == 'editlist':
                    class _Ctx:
                        args = [str(def_id)]
                    await self.edittask_command(update, _Ctx())
                else:
                    kb = [
                        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"dellist_confirm_{def_id}_{page}")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data=f"dellist_page_{page}")]
                    ]
                    await query.edit_message_text("Удалить задачу? Это действие отменит расписание.", reply_markup=InlineKeyboardMarkup(kb))
                return
            if parts[1] == 'cancel':
                await query.edit_message_text("Действие отменено.")
                return
            if parts[1] == 'confirm':
                def_id = int(parts[2])
                page = int(parts[3]) if len(parts) > 3 else 0
                ok = self.db.deactivate_task_definition(user_id, def_id)
                if ok:
                    self.unschedule_task_definition(chat_id, def_id)
                defs_after = self.db.list_task_definitions(user_id)
                if not defs_after:
                    await query.edit_message_text("🗑️ Задача удалена. Активных задач не осталось.")
                    return
                # Подготовим безопасную страницу (учитываем возможное сокращение списка)
                total_pages = max(1, (len(defs_after) - 1) // 5 + 1)
                safe_page = min(page, total_pages - 1)
                await self.show_task_picker(chat_id, user_id, 'delete', page=safe_page, query=query)
                return
            # Если формат данных неизвестен, продолжаем другие ветки
        
        if data.startswith('quick_') or data.startswith('check_'):
            parts = data.split('_')
            action = parts[1]  # yes или no
            task_type = parts[2]
            date = parts[3]
            
            completed = action == 'yes'
            self.db.mark_task_completed(task_type, date, completed)
            
            status_emoji = "✅" if completed else "❌"
            status_text = "выполнено" if completed else "не выполнено"
            
            await query.edit_message_text(
                f"{status_emoji} Задача {task_type} отмечена как {status_text}",
                reply_markup=None
            )
            # Если выполнено — предложим оставить комментарий
            if completed:
                context.user_data['awaiting_comment'] = {"task_type": task_type, "date": date}
                skip_keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⏭️ Пропустить", callback_data=f"skip_comment_{task_type}_{date}")]]
                )
                await self.send_message_to_user(
                    "📝 Хотите оставить короткий комментарий о практике? Просто отправьте сообщение в ответ.",
                    reply_markup=skip_keyboard
                )
            return

        if data.startswith('skip_comment_'):
            parts = data.split('_')
            task_type = parts[2]
            date = parts[3]
            awaiting = context.user_data.get('awaiting_comment')
            if awaiting and awaiting.get('task_type') == task_type and awaiting.get('date') == date:
                context.user_data.pop('awaiting_comment', None)
            await query.edit_message_text("✅ Комментарий пропущен.")
            return

        # ----- V2 callbacks -----
        if data.startswith('v2_quick_') or data.startswith('v2_check_'):
            parts = data.split('_')
            # v2_quick_yes_{defId}_{date}
            action = parts[2]
            def_id = int(parts[3])
            date = parts[4]
            completed = action == 'yes'
            chat_id = update.effective_chat.id
            user = self.db.get_user_by_chat_id(chat_id)
            if not user:
                return
            user_id = user['id']
            self.db.mark_task_completed_v2(user_id, def_id, date, completed)
            status_emoji = "✅" if completed else "❌"
            status_text = "выполнено" if completed else "не выполнено"
            await query.edit_message_text(
                f"{status_emoji} Задача #{def_id} отмечена как {status_text}",
                reply_markup=None
            )
            if completed:
                context.user_data['awaiting_comment_v2'] = {"def_id": def_id, "date": date}
                skip_keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⏭️ Пропустить", callback_data=f"v2_skip_comment_{def_id}_{date}")]]
                )
                await self.send_message_to_chat(
                    chat_id,
                    "📝 Хотите оставить короткий комментарий? Просто отправьте сообщение в ответ.",
                    reply_markup=skip_keyboard
                )
            return

        # ----- Панель управления задачей -----
        if data.startswith('manage_def_'):
            chat_id = update.effective_chat.id
            def_id = int(data.split('_')[-1])
            user = self.db.get_user_by_chat_id(chat_id)
            if not user:
                return
            user_id = user['id']
            d = self.db.get_task_definition(user_id, def_id)
            if not d:
                await query.edit_message_text("Задача не найдена.")
                return
            kb = [
                [InlineKeyboardButton("✏️ Редактировать", callback_data=f"panel_edit_{def_id}")],
                [InlineKeyboardButton("🗑️ Удалить", callback_data=f"panel_delete_confirm_{def_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="panel_back_mytasks")]
            ]
            await query.edit_message_text(f"Управление задачей #{def_id} — {d.get('name')}", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data == 'panel_back_mytasks':
            # Вызовем заново список задач
            fake_update = Update.de_json(update.to_dict(), update._bot)
            # Проще: просто отправим команду mytasks
            await self.mytasks_command(update, context)
            return

        if data.startswith('panel_edit_'):
            chat_id = update.effective_chat.id
            def_id = int(data.split('_')[-1])
            # Инициируем редактирование как через /edittask
            class _Ctx:
                args = [str(def_id)]
            await self.edittask_command(update, _Ctx())
            return

        if data.startswith('panel_delete_confirm_'):
            def_id = int(data.split('_')[-1])
            kb = [
                [InlineKeyboardButton("✅ Да, удалить", callback_data=f"panel_delete_{def_id}")],
                [InlineKeyboardButton("❌ Отмена", callback_data=f"manage_def_{def_id}")]
            ]
            await query.edit_message_text("Удалить задачу? Это действие отменит расписание.", reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith('panel_delete_'):
            chat_id = update.effective_chat.id
            def_id = int(data.split('_')[-1])
            user = self.db.get_user_by_chat_id(chat_id)
            if not user:
                return
            ok = self.db.deactivate_task_definition(user['id'], def_id)
            if ok:
                self.unschedule_task_definition(chat_id, def_id)
                await query.edit_message_text("🗑️ Задача удалена и расписание очищено.")
            else:
                await query.edit_message_text("Задача не найдена или уже удалена.")
            return

        if data == 'start_addtask':
            # Запуск мастера добавления
            class _Ctx:
                args = []
            await self.addtask_command(update, _Ctx())
            return

        if data.startswith('v2_skip_comment_'):
            parts = data.split('_')
            def_id = int(parts[3])
            date = parts[4]
            awaiting = context.user_data.get('awaiting_comment_v2')
            if awaiting and awaiting.get('def_id') == def_id and awaiting.get('date') == date:
                context.user_data.pop('awaiting_comment_v2', None)
            await query.edit_message_text("✅ Комментарий пропущен.")
            return

        if data.startswith('v2_snooze_'):
            parts = data.split('_')
            def_id = int(parts[2])
            minutes = int(parts[4]) if len(parts) > 4 else 30
            chat_id = update.effective_chat.id
            user = self.db.get_user_by_chat_id(chat_id)
            if not user:
                await query.edit_message_text("Начните с /start")
                return
            user_id = user['id']
            task_def = self.db.get_task_definition(user_id, def_id)
            if not task_def:
                await query.edit_message_text("Задача не найдена.")
                return
            self.schedule_snoozed_reminder(chat_id, user_id, def_id, task_def.get('name'), minutes)
            tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
            new_time = datetime.datetime.now(tz) + datetime.timedelta(minutes=minutes)
            date_label = new_time.strftime('%Y-%m-%d %H:%M')
            await query.edit_message_text(f"⏰ Напомню позже в {date_label} ({self._format_timezone(self.db.get_user_timezone(user_id))}).")
            return

        if data.startswith('v2_skip_reminder_'):
            await query.edit_message_text("🛑 Напоминание пропущено. Контроль придет по расписанию.")
            return

        # ----- Добавление задачи: выбор периодичности и дней -----
        if data.startswith('addtask_freq_'):
            freq = data.split('_')[2]
            chat_id = update.effective_chat.id
            st = self.add_task_state.get(chat_id) or {}
            if freq == 'once':
                st['frequency'] = 'once'
            else:
                st['frequency'] = 'daily' if freq == 'daily' else 'weekly'
            self.add_task_state[chat_id] = st
            if st['frequency'] == 'once':
                st['awaiting'] = 'one_time_date'
                await query.edit_message_text("Укажите дату (одноразовая задача) в формате YYYY-MM-DD")
            elif st['frequency'] == 'daily':
                await query.edit_message_text("Вы выбрали: ежедневно. Укажите время напоминания в формате HH:MM")
                st['awaiting'] = 'reminder_time'
            else:
                st.setdefault('days', [])
                await query.edit_message_text("Выберите дни недели. Нажимайте, затем 'Готово'.", reply_markup=self.build_days_keyboard(st['days']))
            return

        if data.startswith('addtask_day_'):
            chat_id = update.effective_chat.id
            day = int(data.split('_')[2])
            st = self.add_task_state.get(chat_id) or {}
            chosen = set(st.get('days', []))
            if day in chosen:
                chosen.remove(day)
            else:
                chosen.add(day)
            st['days'] = sorted(chosen)
            self.add_task_state[chat_id] = st
            await query.edit_message_reply_markup(reply_markup=self.build_days_keyboard(st['days']))
            return

        if data == 'addtask_days_done':
            chat_id = update.effective_chat.id
            st = self.add_task_state.get(chat_id) or {}
            if not st.get('days'):
                await query.answer("Выберите хотя бы один день", show_alert=True)
                return
            st['awaiting'] = 'reminder_time'
            await query.edit_message_text("Укажите время напоминания в формате HH:MM")
            return

        if data == 'addtask_cancel':
            chat_id = update.effective_chat.id
            self.add_task_state.pop(chat_id, None)
            await query.edit_message_text("❌ Добавление задачи отменено")
            return

        # ----- Редактирование задачи -----
        if data.startswith('edittask_field_'):
            chat_id = update.effective_chat.id
            st = self.edit_task_state.get(chat_id)
            if not st:
                await query.answer("Нет активного редактирования", show_alert=True)
                return
            field = data.split('_')[-1]
            if field == 'name':
                st['awaiting'] = 'name'
                await query.edit_message_text("Введите новое название задачи:")
            elif field == 'freq':
                keyboard = [[
                    InlineKeyboardButton("Ежедневно", callback_data="edittask_freq_daily"),
                    InlineKeyboardButton("По дням недели", callback_data="edittask_freq_weekly"),
                    InlineKeyboardButton("Одноразово", callback_data="edittask_freq_once")
                ], [InlineKeyboardButton("Отмена", callback_data="edittask_cancel")]]
                await query.edit_message_text("Выберите периодичность:", reply_markup=InlineKeyboardMarkup(keyboard))
            elif field == 'days':
                days = st.get('data', {}).get('days') or []
                await query.edit_message_text("Выберите дни недели. Нажимайте, затем 'Готово'.", reply_markup=self.build_days_keyboard_edit(days))
            elif field == 'reminder':
                st['awaiting'] = 'reminder_time'
                await query.edit_message_text("Введите новое время напоминания HH:MM:", reply_markup=self.build_edit_menu_keyboard())
            elif field == 'check':
                st['awaiting'] = 'check_time'
                await query.edit_message_text("Введите новое время контроля HH:MM:", reply_markup=self.build_edit_menu_keyboard())
            elif field == 'date':
                st['awaiting'] = 'one_time_date'
                await query.edit_message_text("Введите дату одноразовой задачи в формате YYYY-MM-DD:", reply_markup=self.build_edit_menu_keyboard())
            return

        if data.startswith('edittask_freq_'):
            chat_id = update.effective_chat.id
            st = self.edit_task_state.get(chat_id) or {}
            freq = data.split('_')[2]
            st.setdefault('data', {})
            if freq == 'once':
                st['data']['frequency'] = 'once'
                st['data']['days'] = []
            else:
                st['data']['frequency'] = 'daily' if freq == 'daily' else 'weekly'
                if st['data']['frequency'] == 'daily':
                    st['data']['days'] = list(range(7))
            self.edit_task_state[chat_id] = st
            await query.edit_message_text("Периодичность обновлена. Нажмите Сохранить или продолжите менять поля.", reply_markup=self.build_edit_menu_keyboard())
            return

        if data.startswith('edittask_day_'):
            chat_id = update.effective_chat.id
            day = int(data.split('_')[2])
            st = self.edit_task_state.get(chat_id) or {}
            st.setdefault('data', {})
            chosen = set(st['data'].get('days') or [])
            if day in chosen:
                chosen.remove(day)
            else:
                chosen.add(day)
            st['data']['days'] = sorted(chosen)
            self.edit_task_state[chat_id] = st
            await query.edit_message_reply_markup(reply_markup=self.build_days_keyboard_edit(st['data']['days']))
            return

        if data == 'edittask_days_done':
            await query.edit_message_text("Дни обновлены. Нажмите Сохранить или продолжите менять поля.", reply_markup=self.build_edit_menu_keyboard())
            return

        if data == 'edittask_cancel':
            chat_id = update.effective_chat.id
            self.edit_task_state.pop(chat_id, None)
            await query.edit_message_text("❌ Редактирование отменено")
            return

        if data == 'edittask_save':
            chat_id = update.effective_chat.id
            st = self.edit_task_state.get(chat_id)
            if not st:
                await query.answer("Нет активного редактирования", show_alert=True)
                return
            user_id = st['user_id']
            def_id = st['def_id']
            data_to_save = st.get('data', {})
            self.db.update_task_definition(
                user_id,
                def_id,
                name=data_to_save.get('name'),
                frequency=data_to_save.get('frequency'),
                days=data_to_save.get('days'),
                reminder_time=data_to_save.get('reminder_time'),
                check_time=data_to_save.get('check_time'),
                one_time_date=data_to_save.get('one_time_date')
            )
            self.unschedule_task_definition(chat_id, def_id)
            new_def = self.db.get_task_definition(user_id, def_id)
            if new_def:
                self.schedule_task_definition(chat_id, user_id, new_def)
            self.edit_task_state.pop(chat_id, None)
            await query.edit_message_text("✅ Изменения сохранены и расписание обновлено!")
            return

    async def comment_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений: комментарии и мастер добавления задач"""
        text = (update.message.text or '').strip()
        chat_id = update.effective_chat.id

        # -1) Установка timezone
        if context.user_data.get('awaiting_timezone'):
            parsed = self._parse_timezone_input(text)
            if not parsed:
                await update.message.reply_text("Не понял часовой пояс. Пример: Europe/Paris или UTC+03:00 (или +3).")
                return
            user = self.db.get_user_by_chat_id(chat_id)
            if not user:
                user_id = self.db.upsert_user(chat_id, update.effective_user.username)
            else:
                user_id = user['id']
            self.db.set_user_timezone(user_id, parsed)
            # Перепланируем
            self.unschedule_all_for_chat(chat_id)
            self.schedule_all_for_user(chat_id, user_id)
            context.user_data.pop('awaiting_timezone', None)
            await update.message.reply_text(f"✅ Часовой пояс сохранен: {self._format_timezone(parsed)}. Расписание обновлено.")
            return
        
        # 0) Редактирование задач: обработка полей
        st_edit = self.edit_task_state.get(chat_id)
        if st_edit:
            awaiting_kind = st_edit.get('awaiting')
            if awaiting_kind == 'name':
                if not text:
                    await update.message.reply_text("Введите непустое название")
                    return
                st_edit.setdefault('data', {})
                st_edit['data']['name'] = text[:64]
                st_edit['awaiting'] = None
                await self.send_message_to_chat(chat_id, "Название обновлено. Нажмите Сохранить или продолжите менять поля.", self.build_edit_menu_keyboard())
                return
            if awaiting_kind == 'reminder_time':
                if not utils.validate_time_format(text):
                    await update.message.reply_text("Неверный формат. Введите время как HH:MM")
                    return
                st_edit.setdefault('data', {})
                st_edit['data']['reminder_time'] = text
                st_edit['awaiting'] = None
                await self.send_message_to_chat(chat_id, "Время напоминания обновлено. Нажмите Сохранить или продолжите менять поля.", self.build_edit_menu_keyboard())
                return
            if awaiting_kind == 'check_time':
                if not utils.validate_time_format(text):
                    await update.message.reply_text("Неверный формат. Введите время как HH:MM")
                    return
                st_edit.setdefault('data', {})
                st_edit['data']['check_time'] = text
                st_edit['awaiting'] = None
                await self.send_message_to_chat(chat_id, "Время контроля обновлено. Нажмите Сохранить или продолжите менять поля.", self.build_edit_menu_keyboard())
                return
            if awaiting_kind == 'one_time_date':
                if not utils.validate_date_format(text):
                    await update.message.reply_text("Неверный формат даты. Используйте YYYY-MM-DD")
                    return
                tz = self._tzinfo_from_string(self.db.get_user_timezone(st_edit['user_id']))
                date_obj = datetime.datetime.strptime(text, '%Y-%m-%d').date()
                if date_obj < datetime.datetime.now(tz).date():
                    await update.message.reply_text("Дата уже прошла. Укажите будущую дату.")
                    return
                st_edit.setdefault('data', {})
                st_edit['data']['one_time_date'] = text
                st_edit['awaiting'] = None
                await self.send_message_to_chat(chat_id, "Дата одноразовой задачи обновлена. Нажмите Сохранить или продолжите менять поля.", self.build_edit_menu_keyboard())
                return

        # 1) Комментарии v1
        awaiting = context.user_data.get('awaiting_comment')
        if awaiting:
            task_type = awaiting['task_type']
            date = awaiting['date']
            if not text:
                await update.message.reply_text("Комментарий пуст. Отправьте текст или нажмите Пропустить.")
                return
            self.db.set_task_comment(task_type, date, text)
            context.user_data.pop('awaiting_comment', None)
            await update.message.reply_text("💾 Комментарий сохранен. Спасибо!")
            return

        # 2) Комментарии v2
        awaiting_v2 = context.user_data.get('awaiting_comment_v2')
        if awaiting_v2:
            user = self.db.get_user_by_chat_id(chat_id)
            if user:
                user_id = user['id']
                def_id = awaiting_v2['def_id']
                date_v2 = awaiting_v2['date']
                if text:
                    self.db.set_task_comment_v2(user_id, def_id, date_v2, text)
                    await update.message.reply_text("💾 Комментарий сохранен. Спасибо!")
                context.user_data.pop('awaiting_comment_v2', None)
                return

        # 3) Мастер добавления задач
        st = self.add_task_state.get(chat_id)
        if not st:
            return
        if st.get('step') == 'name':
            if not text:
                await update.message.reply_text("Введите непустое название")
                return
            st['name'] = text[:64]
            st['step'] = 'frequency'
            keyboard = [[
                InlineKeyboardButton("Ежедневно", callback_data="addtask_freq_daily"),
                InlineKeyboardButton("По дням недели", callback_data="addtask_freq_weekly")
            ], [
                InlineKeyboardButton("Одноразово", callback_data="addtask_freq_once")
            ], [
                InlineKeyboardButton("Отмена", callback_data="addtask_cancel")
            ]]
            await update.message.reply_text("Выберите периодичность:", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        awaiting_kind = st.get('awaiting')
        if awaiting_kind == 'one_time_date':
            if not utils.validate_date_format(text):
                await update.message.reply_text("Неверный формат даты. Используйте YYYY-MM-DD")
                return
            tz = self._tzinfo_from_string(self.db.get_user_timezone(st.get('user_id')))
            date_obj = datetime.datetime.strptime(text, '%Y-%m-%d').date()
            if date_obj < datetime.datetime.now(tz).date():
                await update.message.reply_text("Дата уже прошла. Укажите будущую дату.")
                return
            st['one_time_date'] = text
            st['awaiting'] = 'reminder_time'
            await update.message.reply_text("Укажите время напоминания в формате HH:MM")
            return
        if awaiting_kind == 'reminder_time':
            if not utils.validate_time_format(text):
                await update.message.reply_text("Неверный формат. Введите время как HH:MM")
                return
            st['reminder_time'] = text
            st['awaiting'] = 'check_time'
            await update.message.reply_text("Введите время контроля HH:MM")
            return
        if awaiting_kind == 'check_time':
            if not utils.validate_time_format(text):
                await update.message.reply_text("Неверный формат. Введите время как HH:MM")
                return
            st['check_time'] = text
            user = self.db.get_user_by_chat_id(chat_id)
            if not user:
                user_id = self.db.upsert_user(chat_id, update.effective_user.username)
            else:
                user_id = user['id']
            frequency = st.get('frequency') or 'daily'
            if frequency == 'weekly':
                days = st.get('days') or []
            elif frequency == 'daily':
                days = list(range(7))
            else:
                days = []
            def_id = self.db.add_task_definition(user_id, st['name'], frequency, days or list(range(7)), st['reminder_time'], st['check_time'], st.get('one_time_date'))
            # Планируем
            saved_defs = self.db.list_task_definitions(user_id)
            target_def = next((d for d in saved_defs if d['id'] == def_id), None)
            if target_def:
                self.schedule_task_definition(chat_id, user_id, target_def)
                tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
                calendar_link = self._build_calendar_link_for_def(target_def, tz)
                if calendar_link:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Добавить в Google Calendar", url=calendar_link)]])
                    await self.send_message_to_chat(chat_id, "📅 Добавить задачу в Google Calendar?", kb)
            await update.message.reply_text("✅ Задача добавлена и запланирована!")
            self.add_task_state.pop(chat_id, None)
            return
    
    async def today_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /today"""
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await self.send_message_to_chat(chat_id, "Начните с /start")
            return
        user_id = user['id']
        tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
        now = datetime.datetime.now(tz)
        today_str = now.strftime('%Y-%m-%d')
        weekday = now.weekday()
        defs = self.db.list_task_definitions(user_id)
        tasks_in_db = {t.get('task_def_id'): t for t in self.db.get_tasks_for_date_by_user(user_id, today_str)}
        scheduled_today = []
        for d in defs:
            freq = d.get('frequency')
            if freq == 'once':
                if d.get('one_time_date') == today_str:
                    scheduled_today.append((d['id'], d['name']))
            else:
                days_list = d.get('days_list') or list(range(7))
                if weekday in days_list:
                    scheduled_today.append((d['id'], d['name']))
        if not scheduled_today:
            await self.send_message_to_chat(chat_id, f"📅 На сегодня ({today_str}) задач нет по расписанию.")
            return
        message = f"📋 Задачи на сегодня ({today_str}):\n\n"
        keyboard = []
        for def_id, display_name in scheduled_today:
            if def_id in tasks_in_db:
                status = "✅" if tasks_in_db[def_id].get('completed') else "⏳"
            else:
                status = "⏳"
            message += f"• {display_name}: {status}\n"
            keyboard.append([
                InlineKeyboardButton(f"✅ {display_name}", callback_data=f"v2_quick_yes_{def_id}_{today_str}"),
                InlineKeyboardButton("❌", callback_data=f"v2_quick_no_{def_id}_{today_str}")
            ])
        await self.send_message_to_chat(chat_id, message, InlineKeyboardMarkup(keyboard))
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stats"""
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await update.message.reply_text("Начните с /start")
            return
        user_id = user['id']
        tz = self._tzinfo_from_string(self.db.get_user_timezone(user_id))
        today = datetime.datetime.now(tz).strftime('%Y-%m-%d')
        stats = self.db.get_completion_stats_by_user(user_id, today, today)
        message = f"📊 Статистика на сегодня:\n\n"
        message += f"• Всего задач: {stats['total_tasks']}\n"
        message += f"• Выполнено: {stats['completed_tasks']}\n"
        message += f"• Процент выполнения: {stats['completion_rate']}%"
        await update.message.reply_text(message)

    async def report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await update.message.reply_text("Начните с /start")
            return
        # Пытаемся отправить полноценный отчет. Если что-то пойдет не так, даем простой ответ, чтобы команда не молчала.
        try:
            await self.send_daily_report_v2(chat_id, user['id'])
        except Exception as e:
            logger.error(f"/report: ошибка при формировании отчета: {e}")
            try:
                tz = self._tzinfo_from_string(self.db.get_user_timezone(user['id']))
                today = datetime.datetime.now(tz).strftime('%Y-%m-%d')
                stats = self.db.get_completion_stats_by_user(user['id'], today, today)
                msg = (
                    "📊 Отчет за сегодня (упрощенный):\n\n"
                    f"• Всего задач: {stats['total_tasks']}\n"
                    f"• Выполнено: {stats['completed_tasks']}\n"
                    f"• Процент выполнения: {stats['completion_rate']}%"
                )
                await update.message.reply_text(msg)
            except Exception as inner_e:
                logger.error(f"/report: ошибка резервного ответа: {inner_e}")
                await update.message.reply_text("Не удалось сформировать отчет. Попробуйте позже.")

    async def addtask_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            user_id = self.db.upsert_user(chat_id, update.effective_user.username)
        else:
            user_id = user['id']
        if self.db.count_task_definitions(user_id) >= 10:
            await self.send_message_to_chat(chat_id, "Вы достигли лимита 10 задач.")
            return
        self.add_task_state[chat_id] = {'user_id': user_id, 'step': 'name'}
        await self.send_message_to_chat(chat_id, "Введите короткое название задачи (например, 'Медитация'):")

    async def edittask_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await self.send_message_to_chat(chat_id, "Начните с /start")
            return
        user_id = user['id']
        args = context.args if hasattr(context, 'args') else []
        if not args:
            await self.show_task_picker(chat_id, user_id, action="edit", page=0)
            return
        try:
            def_id = int(args[0])
        except ValueError:
            await self.send_message_to_chat(chat_id, "Неверный id. Пример: /edittask 3")
            return
        d = self.db.get_task_definition(user_id, def_id)
        if not d:
            await self.send_message_to_chat(chat_id, "Задача не найдена или уже удалена.")
            return
        self.edit_task_state[chat_id] = {
            'user_id': user_id,
            'def_id': def_id,
            'data': {
                'name': d.get('name'),
                'frequency': d.get('frequency'),
                'days': d.get('days_list') or [],
                'reminder_time': d.get('reminder_time'),
                'check_time': d.get('check_time'),
                'one_time_date': d.get('one_time_date')
            },
            'awaiting': None
        }
        await self.send_message_to_chat(chat_id, "Что изменить?", self.build_edit_menu_keyboard())

    async def deletetask_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await self.send_message_to_chat(chat_id, "Начните с /start")
            return
        user_id = user['id']
        args = context.args if hasattr(context, 'args') else []
        if not args:
            await self.show_task_picker(chat_id, user_id, action="delete", page=0)
            return
        try:
            def_id = int(args[0])
        except ValueError:
            await self.send_message_to_chat(chat_id, "Неверный id. Пример: /deletetask 3")
            return
        ok = self.db.deactivate_task_definition(user_id, def_id)
        if not ok:
            await self.send_message_to_chat(chat_id, "Задача не найдена или уже удалена.")
            return
        self.unschedule_task_definition(chat_id, def_id)
        await self.send_message_to_chat(chat_id, "🗑️ Задача удалена и расписание очищено.")

    async def mytasks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await self.send_message_to_chat(chat_id, "Начните с /start")
            return
        defs = self.db.list_task_definitions(user['id'])
        if not defs:
            kb = [[InlineKeyboardButton("➕ Добавить задачу", callback_data="start_addtask")]]
            await self.send_message_to_chat(chat_id, "У вас пока нет задач. Нажмите, чтобы добавить:", InlineKeyboardMarkup(kb))
            return
        lines = ["Ваши задачи (нажмите, чтобы управлять):"]
        days_names = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
        for d in defs:
            freq_value = d.get('frequency')
            if freq_value == 'daily':
                freq = 'Ежедневно'
                days = d.get('days_list') or list(range(7))
                freq_details = f"дни: {','.join(days_names[i] for i in days)}"
            elif freq_value == 'weekly':
                freq = 'По дням недели'
                days = d.get('days_list') or []
                freq_details = f"дни: {','.join(days_names[i] for i in days)}"
            else:
                freq = 'Одноразово'
                freq_details = f"дата: {d.get('one_time_date') or '?'}"
            lines.append(f"• #{d['id']} {d['name']} — {freq}, {freq_details}, напоминание {d['reminder_time']}, контроль {d['check_time']}")
        kb_rows = []
        for d in defs:
            kb_rows.append([InlineKeyboardButton(f"✏️ {d['name']} (#{d['id']})", callback_data=f"manage_def_{d['id']}")])
        kb_rows.append([InlineKeyboardButton("➕ Добавить задачу", callback_data="start_addtask")])
        await self.send_message_to_chat(chat_id, '\n'.join(lines), InlineKeyboardMarkup(kb_rows))

    async def show_days_keyboard(self, chat_id: int):
        st = self.add_task_state.get(chat_id) or {}
        chosen = set(st.get('days', []))
        days_names = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
        rows = []
        for i in range(0, 7, 2):
            row = []
            for d in [i, i+1] if i+1 < 7 else [i]:
                label = ("✅ " if d in chosen else "") + days_names[d]
                row.append(InlineKeyboardButton(label, callback_data=f"addtask_day_{d}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("Готово", callback_data="addtask_days_done")])
        markup = InlineKeyboardMarkup(rows)
        await self.send_message_to_chat(chat_id, "Выберите дни недели:", markup)
    
    def unschedule_all_for_chat(self, chat_id: int):
        """Удалить все задания напоминаний/проверок и отчётов для указанного чата."""
        try:
            for job in list(self.scheduler.get_jobs()):
                jid = getattr(job, 'id', '')
                if not isinstance(jid, str):
                    continue
                if (
                    jid == f'daily_report_{chat_id}' or
                    jid == f'weekly_report_{chat_id}' or
                    jid.startswith(f'v2_reminder_{chat_id}_') or
                    jid.startswith(f'v2_check_{chat_id}_') or
                    jid.startswith(f'snooze_{chat_id}_')
                ):
                    try:
                        self.scheduler.remove_job(jid)
                    except Exception:
                        pass
        except Exception:
            pass
    
    async def start_bot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start_bot"""
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await update.message.reply_text("Начните с /start")
            return
        user_id = user['id']
        # Перепланируем все задачи и отчёты только для текущего чата
        self.schedule_all_for_user(chat_id, user_id)
        # На всякий случай запускаем планировщик, если он не запущен (глобально)
        if not self.scheduler.running:
            self.scheduler.start()
        await update.message.reply_text("🤖 Напоминания и отчёты для этого чата включены.")
    
    async def stop_bot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stop_bot"""
        chat_id = update.effective_chat.id
        # Снимаем все задания только для текущего чата, не останавливая глобальный планировщик
        self.unschedule_all_for_chat(chat_id)
        await update.message.reply_text("⏹️ Напоминания и отчёты для этого чата отключены.")

# Глобальная переменная для хранения экземпляра бота
bot_instance = None

async def main():
    """Основная функция"""
    global bot_instance
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен!")
        return
    
    # Создаем экземпляр бота
    bot_instance = TaskAssistantBot()
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Устанавливаем команды бота для меню
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "Запуск и регистрация"),
            BotCommand("help", "Помощь по командам"),
            BotCommand("addtask", "Добавить задачу"),
            BotCommand("mytasks", "Мои задачи"),
            BotCommand("edittask", "Редактировать задачу"),
            BotCommand("deletetask", "Удалить задачу"),
            BotCommand("report", "Отчет за сегодня"),
            BotCommand("timezone", "Часовой пояс"),
        ])
    except Exception as e:
        logger.error(f"Не удалось установить команды бота: {e}")
    
    async def send_message_to_chat(chat_id: int, message: str, reply_markup=None):
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=message,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
    bot_instance.send_message_to_chat = send_message_to_chat
    
    # Автопланирование для всех существующих пользователей при старте (после рестарта сервиса)
    try:
        users = bot_instance.db.list_users()
        for u in users:
            chat_id = u.get('chat_id')
            user_id = u.get('id')
            if chat_id and user_id:
                bot_instance.schedule_all_for_user(chat_id, user_id)
        logger.info(f"Инициализировано расписание для {len(users)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка автопланирования пользователей при старте: {e}")
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", bot_instance.start))
    application.add_handler(CommandHandler("help", bot_instance.help_command))
    application.add_handler(CommandHandler("today", bot_instance.today_command))
    application.add_handler(CommandHandler("stats", bot_instance.stats_command))
    application.add_handler(CommandHandler("report", bot_instance.report_command))
    application.add_handler(CommandHandler("start_bot", bot_instance.start_bot_command))
    application.add_handler(CommandHandler("stop_bot", bot_instance.stop_bot_command))
    application.add_handler(CommandHandler("addtask", bot_instance.addtask_command))
    application.add_handler(CommandHandler("mytasks", bot_instance.mytasks_command))
    application.add_handler(CommandHandler("edittask", bot_instance.edittask_command))
    application.add_handler(CommandHandler("deletetask", bot_instance.deletetask_command))
    application.add_handler(CommandHandler("timezone", bot_instance.timezone_command))
    
    # Добавляем обработчик кнопок
    application.add_handler(CallbackQueryHandler(bot_instance.button_callback))
    # Обработчик текстовых сообщений как комментариев
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_instance.comment_message_handler))
    
    # Запускаем планировщик
    bot_instance.scheduler.start()
    
    logger.info("Бот запущен!")
    
    try:
        # Инициализируем приложение
        await application.initialize()
        
        # Запускаем polling
        await application.start()
        await application.updater.start_polling()
        
        logger.info("Бот успешно запущен и работает!")
        
        # Ждем бесконечно, пока не получим сигнал остановки
        import signal
        import asyncio
        
        shutting_down = False
        stop_event = asyncio.Event()
        
        def signal_handler():
            logger.info("Получен сигнал остановки")
            asyncio.create_task(shutdown())
        
        async def shutdown():
            nonlocal shutting_down
            if shutting_down:
                return
            shutting_down = True
            logger.info("Начинаем остановку бота...")
            # Останавливаем polling только если он запущен
            try:
                if getattr(application, 'updater', None):
                    await application.updater.stop()
            except RuntimeError:
                # Updater уже остановлен
                pass
            except Exception as e:
                logger.error(f"Ошибка при остановке updater: {e}")
            
            await application.stop()
            await application.shutdown()
            
            # Останавливаем планировщик
            if bot_instance.scheduler.running:
                logger.info("Остановка планировщика...")
                bot_instance.scheduler.shutdown()
            logger.info("Бот остановлен")
            # Сигнализируем главному циклу завершиться
            try:
                stop_event.set()
            except Exception:
                pass
        
        # Регистрируем обработчик сигналов
        for sig in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(sig, lambda s, f: signal_handler())
        
        # Ждем бесконечно
        # Ожидаем завершения (сигнал от shutdown)
        await stop_event.wait()
            
    except Exception as e:
        logger.error(f"Ошибка при работе бота: {e}")
        # Останавливаем планировщик при ошибке
        if bot_instance.scheduler.running:
            logger.info("Остановка планировщика...")
            bot_instance.scheduler.shutdown()
        raise

# Убрано asyncio.run() отсюда, чтобы избежать конфликта с run.py
# if __name__ == '__main__':
#     import asyncio
#     asyncio.run(main())
