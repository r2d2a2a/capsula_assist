import logging
import datetime
from typing import Dict, List
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import BOT_TOKEN, USER_ID, TIMEZONE, TASKS_SCHEDULE
from database import TaskDatabase
import utils

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TaskAssistantBot:
    def __init__(self):
        self.db = TaskDatabase()
        # Принудительно используем московский часовой пояс
        self.moscow_tz = pytz.timezone('Europe/Moscow')
        self.scheduler = AsyncIOScheduler(timezone=self.moscow_tz)
        self.add_task_state: Dict[int, Dict] = {}
        self.setup_scheduler()
    
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
        daily_id = f'daily_report_{chat_id}'
        weekly_id = f'weekly_report_{chat_id}'
        self.scheduler.add_job(
            self.send_daily_report_v2,
            CronTrigger(hour=20, minute=0, timezone=self.moscow_tz),
            args=[chat_id, user_id],
            id=daily_id,
            replace_existing=True
        )
        self.scheduler.add_job(
            self.send_weekly_report_v2,
            CronTrigger(day_of_week=6, hour=20, minute=30, timezone=self.moscow_tz),
            args=[chat_id, user_id],
            id=weekly_id,
            replace_existing=True
        )

    def schedule_task_definition(self, chat_id: int, user_id: int, task_def: Dict):
        days: List[int] = task_def.get('days_list') or list(range(7))
        rh, rm = map(int, task_def['reminder_time'].split(':'))
        ch, cm = map(int, task_def['check_time'].split(':'))
        def_id = task_def['id']
        name = task_def['name']
        for day in days:
            r_job_id = f'v2_reminder_{chat_id}_{def_id}_{day}_{rh:02d}{rm:02d}'
            c_job_id = f'v2_check_{chat_id}_{def_id}_{day}_{ch:02d}{cm:02d}'
            self.scheduler.add_job(
                self.send_task_reminder_v2,
                CronTrigger(day_of_week=day, hour=rh, minute=rm, timezone=self.moscow_tz),
                args=[chat_id, user_id, def_id, name],
                id=r_job_id,
                replace_existing=True
            )
            self.scheduler.add_job(
                self.send_completion_check_v2,
                CronTrigger(day_of_week=day, hour=ch, minute=cm, timezone=self.moscow_tz),
                args=[chat_id, user_id, def_id, name],
                id=c_job_id,
                replace_existing=True
            )

    def schedule_all_for_user(self, chat_id: int, user_id: int):
        defs = self.db.list_task_definitions(user_id)
        for d in defs:
            self.schedule_task_definition(chat_id, user_id, d)
        self.schedule_reports_for_user(chat_id, user_id)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        chat_id = update.effective_chat.id
        username = update.effective_user.username
        user_id = self.db.upsert_user(chat_id, username)
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
        await update.message.reply_text("Добавьте свою задачу командой /addtask. Посмотреть список: /mytasks")
    
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
 
🔧 Управление:
/start_bot - Запустить напоминания
/stop_bot - Остановить напоминания
        """
        await update.message.reply_text(help_text)
    
    async def send_task_reminder(self, task_type: str, task_name: str):
        """Отправить напоминание о задаче"""
        try:
            today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
            # Атомарно получаем право на отправку, чтобы избежать дублей
            lock_acquired, _ = self.db.acquire_send_lock(task_type, today)
            if not lock_acquired:
                logger.info(f"Пропускаем дубликат напоминания для {task_type} на {today}")
                return
            # Отправляем напоминание
            message = f"⏰ Напоминание!\n\n📋 Время для: {task_name}\n🕐 {datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')}"
            
            # Создаем клавиатуру для быстрого ответа
            keyboard = [
                [InlineKeyboardButton("✅ Выполнено", callback_data=f"quick_yes_{task_type}_{today}")],
                [InlineKeyboardButton("❌ Не выполнено", callback_data=f"quick_no_{task_type}_{today}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Отправляем сообщение (здесь нужно использовать application.bot)
            # Это будет реализовано в main функции
            await self.send_message_to_user(message, reply_markup)
            
            # Флаг already set в acquire_send_lock
            
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания: {e}")
    
    async def send_task_reminder_v2(self, chat_id: int, user_id: int, task_def_id: int, task_name: str):
        """Многопользовательское напоминание."""
        try:
            today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
            lock_acquired, _ = self.db.acquire_send_lock_v2(user_id, task_def_id, today)
            if not lock_acquired:
                return
            message = f"⏰ Напоминание!\n\n📋 Время для: {task_name}\n🕐 {datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')}"
            keyboard = [
                [InlineKeyboardButton("✅ Выполнено", callback_data=f"v2_quick_yes_{task_def_id}_{today}")],
                [InlineKeyboardButton("❌ Не выполнено", callback_data=f"v2_quick_no_{task_def_id}_{today}")]
            ]
            await self.send_message_to_chat(chat_id, message, InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания v2: {e}")
    
    async def send_completion_check(self, task_type: str, task_name: str):
        """Отправить проверку выполнения задачи"""
        try:
            today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
            
            # Атомарно получаем право на отправку проверки, чтобы избежать дублей
            lock_acquired, _ = self.db.acquire_check_lock(task_type, today)
            if not lock_acquired:
                logger.info(f"Пропускаем дубликат проверки для {task_type} на {today}")
                return
            
            message = f"🔍 Контроль выполнения!\n\n📋 Задача: {task_name}\n⏰ Время проверки: {datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')}\n\nВыполнили ли вы эту задачу?"
            
            keyboard = [
                [InlineKeyboardButton("✅ Да, выполнил", callback_data=f"check_yes_{task_type}_{today}")],
                [InlineKeyboardButton("❌ Нет, не выполнил", callback_data=f"check_no_{task_type}_{today}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.send_message_to_user(message, reply_markup)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке проверки: {e}")
    
    async def send_completion_check_v2(self, chat_id: int, user_id: int, task_def_id: int, task_name: str):
        try:
            today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
            lock_acquired, _ = self.db.acquire_check_lock_v2(user_id, task_def_id, today)
            if not lock_acquired:
                return
            message = f"🔍 Контроль выполнения!\n\n📋 Задача: {task_name}\n⏰ Время проверки: {datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')}\n\nВыполнили ли вы эту задачу?"
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
            today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
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
            today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
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
            today = datetime.datetime.now(pytz.timezone(TIMEZONE))
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
            today = datetime.datetime.now(pytz.timezone(TIMEZONE))
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
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
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

        if data.startswith('v2_skip_comment_'):
            parts = data.split('_')
            def_id = int(parts[3])
            date = parts[4]
            awaiting = context.user_data.get('awaiting_comment_v2')
            if awaiting and awaiting.get('def_id') == def_id and awaiting.get('date') == date:
                context.user_data.pop('awaiting_comment_v2', None)
            await query.edit_message_text("✅ Комментарий пропущен.")
            return

        # ----- Добавление задачи: выбор периодичности и дней -----
        if data.startswith('addtask_freq_'):
            freq = data.split('_')[2]
            chat_id = update.effective_chat.id
            st = self.add_task_state.get(chat_id) or {}
            st['frequency'] = 'daily' if freq == 'daily' else 'weekly'
            self.add_task_state[chat_id] = st
            if st['frequency'] == 'daily':
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

    async def comment_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений: комментарии и мастер добавления задач"""
        text = (update.message.text or '').strip()
        chat_id = update.effective_chat.id
        
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
                InlineKeyboardButton("Отмена", callback_data="addtask_cancel")
            ]]
            await update.message.reply_text("Выберите периодичность:", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        awaiting_kind = st.get('awaiting')
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
            days = st.get('days') if frequency == 'weekly' else list(range(7))
            def_id = self.db.add_task_definition(user_id, st['name'], frequency, days or list(range(7)), st['reminder_time'], st['check_time'])
            # Планируем
            saved_defs = self.db.list_task_definitions(user_id)
            target_def = next((d for d in saved_defs if d['id'] == def_id), None)
            if target_def:
                self.schedule_task_definition(chat_id, user_id, target_def)
            await update.message.reply_text("✅ Задача добавлена и запланирована!")
            self.add_task_state.pop(chat_id, None)
            return
    
    async def today_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /today"""
        tz = pytz.timezone(TIMEZONE)
        now = datetime.datetime.now(tz)
        today_str = now.strftime('%Y-%m-%d')
        weekday = now.weekday()
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await update.message.reply_text("Начните с /start")
            return
        user_id = user['id']
        defs = self.db.list_task_definitions(user_id)
        tasks_in_db = {t.get('task_def_id'): t for t in self.db.get_tasks_for_date_by_user(user_id, today_str)}
        scheduled_today = []
        for d in defs:
            days_list = d.get('days_list') or list(range(7))
            if weekday in days_list:
                scheduled_today.append((d['id'], d['name']))
        if not scheduled_today:
            await update.message.reply_text(f"📅 На сегодня ({today_str}) задач нет по расписанию.")
            return
        message = f"📋 Задачи на сегодня ({today_str}):\n\n"
        for def_id, display_name in scheduled_today:
            if def_id in tasks_in_db:
                status = "✅" if tasks_in_db[def_id].get('completed') else "⏳"
            else:
                status = "⏳"
            message += f"• {display_name}: {status}\n"
        await update.message.reply_text(message)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stats"""
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await update.message.reply_text("Начните с /start")
            return
        user_id = user['id']
        today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
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
        await self.send_daily_report_v2(chat_id, user['id'])

    async def addtask_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            user_id = self.db.upsert_user(chat_id, update.effective_user.username)
        else:
            user_id = user['id']
        if self.db.count_task_definitions(user_id) >= 10:
            await update.message.reply_text("Вы достигли лимита 10 задач.")
            return
        self.add_task_state[chat_id] = {'user_id': user_id, 'step': 'name'}
        await update.message.reply_text("Введите короткое название задачи (например, 'Медитация'):")

    async def mytasks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = self.db.get_user_by_chat_id(chat_id)
        if not user:
            await update.message.reply_text("Начните с /start")
            return
        defs = self.db.list_task_definitions(user['id'])
        if not defs:
            await update.message.reply_text("У вас пока нет задач. Используйте /addtask")
            return
        lines = ["Ваши задачи:"]
        days_names = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
        for d in defs:
            freq = 'Ежедневно' if (d.get('frequency') == 'daily') else 'По дням недели'
            days = d.get('days_list') or list(range(7))
            days_str = ','.join(days_names[i] for i in days)
            lines.append(f"• #{d['id']} {d['name']} — {freq}, дни: {days_str}, напоминание {d['reminder_time']}, контроль {d['check_time']}")
        await update.message.reply_text('\n'.join(lines))

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
    
    async def start_bot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start_bot"""
        if not self.scheduler.running:
            self.scheduler.start()
            await update.message.reply_text("🤖 Бот запущен! Напоминания активированы.")
        else:
            await update.message.reply_text("🤖 Бот уже запущен.")
    
    async def stop_bot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stop_bot"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            await update.message.reply_text("⏹️ Бот остановлен. Напоминания отключены.")
        else:
            await update.message.reply_text("⏹️ Бот уже остановлен.")

# Глобальная переменная для хранения экземпляра бота
bot_instance = None

async def main():
    """Основная функция"""
    global bot_instance
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен!")
        return
    
    if not USER_ID:
        logger.error("USER_ID не установлен!")
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
            BotCommand("today", "Задачи на сегодня"),
            BotCommand("stats", "Статистика за сегодня"),
            BotCommand("report", "Отчет за сегодня"),
            BotCommand("start_bot", "Запустить напоминания"),
            BotCommand("stop_bot", "Остановить напоминания"),
        ])
    except Exception as e:
        logger.error(f"Не удалось установить команды бота: {e}")
    
    # Переопределяем метод отправки сообщений
    async def send_message_to_user(message: str, reply_markup=None):
        try:
            await application.bot.send_message(
                chat_id=USER_ID,
                text=message,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
    
    bot_instance.send_message_to_user = send_message_to_user
    
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
