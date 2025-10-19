import logging
import datetime
from typing import Dict, List
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import BOT_TOKEN, USER_ID, TIMEZONE, TASKS_SCHEDULE
from database import TaskDatabase
from utils import get_moscow_time, format_date, get_day_name, get_motivational_message, get_task_emoji

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ImprovedTaskAssistantBot:
    def __init__(self):
        self.db = TaskDatabase()
        # Принудительно используем московский часовой пояс для планировщика
        self.moscow_tz = pytz.timezone('Europe/Moscow')
        self.scheduler = AsyncIOScheduler(timezone=self.moscow_tz)
        self.setup_scheduler()
        self.user_streak = 0  # Счетчик дней подряд
        self.last_completion_date = None
    
    def setup_scheduler(self):
        """Настройка расписания напоминаний"""
        for task_type, task_config in TASKS_SCHEDULE.items():
            # Напоминание о задаче
            for day in task_config['days']:
                hour, minute = map(int, task_config['time'].split(':'))
                # Добавляем время в ID для уникальности
                job_id = f'reminder_{task_type}_{day}_{hour:02d}{minute:02d}'
                self.scheduler.add_job(
                    self.send_task_reminder,
                    CronTrigger(day_of_week=day, hour=hour, minute=minute, timezone=self.moscow_tz),
                    args=[task_type, task_config['name']],
                    id=job_id,
                    replace_existing=True  # Заменяем существующую задачу, если ID совпадает
                )
            
            # Контроль выполнения
            for day in task_config['days']:
                hour, minute = map(int, task_config['check_time'].split(':'))
                # Добавляем время в ID для уникальности
                job_id = f'check_{task_type}_{day}_{hour:02d}{minute:02d}'
                self.scheduler.add_job(
                    self.send_completion_check,
                    CronTrigger(day_of_week=day, hour=hour, minute=minute, timezone=self.moscow_tz),
                    args=[task_type, task_config['name']],
                    id=job_id,
                    replace_existing=True  # Заменяем существующую задачу, если ID совпадает
                )
        
        # Ежедневный отчет в 22:00
        self.scheduler.add_job(
            self.send_daily_report,
            CronTrigger(hour=22, minute=0, timezone=self.moscow_tz),
            id='daily_report'
        )
        
        # Еженедельный отчет в воскресенье в 22:30
        self.scheduler.add_job(
            self.send_weekly_report,
            CronTrigger(day_of_week='sun', hour=22, minute=30, timezone=self.moscow_tz),
            id='weekly_report'
        )
        # Диагностика таймзоны
        logger.info(f"APScheduler timezone: {self.scheduler.timezone}")
        for job in self.scheduler.get_jobs():
            try:
                next_run = getattr(job, 'next_run_time', None)
            except Exception:
                next_run = None
            logger.info(f"Job {job.id} next run: {next_run}")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная команда /start с лучшим UX"""
        if update.effective_user.id != USER_ID:
            await update.message.reply_text("🔒 Этот бот предназначен только для определенного пользователя.")
            return
        
        # Короткое приветствие
        welcome_text = """👋 Привет! Я твой персональный ассистент задач.

🎯 Я буду помогать тебе с:
• Медитацией в 6:05
• Планированием в 9:00  
• Тренировками/йогой в 15:00

💡 Готов начать? Нажми /help для списка команд."""
        
        await update.message.reply_text(welcome_text)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная команда /help с группировкой"""
        if update.effective_user.id != USER_ID:
            return
        
        help_text = """📚 **Доступные команды:**

📋 **Основные:**
/start - Начать работу
/today - Задачи на сегодня
/stats - Моя статистика

📊 **Отчеты:**
/report - Отчет за сегодня
/week - Отчет за неделю

⚙️ **Управление:**
/start_bot - Включить напоминания
/stop_bot - Выключить напоминания

❓ **Помощь:**
/help - Это сообщение"""
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def send_task_reminder(self, task_type: str, task_name: str):
        """Улучшенное напоминание с персонализацией"""
        try:
            today = get_moscow_time().strftime('%Y-%m-%d')
            current_time = get_moscow_time().strftime('%H:%M')
            # Атомарно получаем право на отправку, чтобы избежать дублей
            lock_acquired, _ = self.db.acquire_send_lock(task_type, today)
            if not lock_acquired:
                logger.info(f"Пропускаем дубликат напоминания для {task_type} на {today}")
                return
            
            # Персонализированное сообщение
            emoji = get_task_emoji(task_type)
            message = f"{emoji} **Время для {task_name}!**\n\n"
            message += f"🕐 {current_time}\n"
            
            # Добавляем мотивацию в зависимости от времени
            hour = get_moscow_time().hour
            if hour < 8:
                message += "🌅 Отличное утро для начала дня!"
            elif hour < 12:
                message += "☀️ Время для продуктивности!"
            else:
                message += "💪 Время для активности!"
            
            # Создаем улучшенную клавиатуру
            keyboard = [
                [InlineKeyboardButton("✅ Готово!", callback_data=f"quick_yes_{task_type}_{today}")],
                [InlineKeyboardButton("⏰ Напомнить позже", callback_data=f"remind_later_{task_type}_{today}")],
                [InlineKeyboardButton("❌ Пропустить", callback_data=f"quick_no_{task_type}_{today}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.send_message_to_user(message, reply_markup)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания: {e}")
    
    async def send_completion_check(self, task_type: str, task_name: str):
        """Улучшенная проверка выполнения"""
        try:
            today = get_moscow_time().strftime('%Y-%m-%d')
            current_time = get_moscow_time().strftime('%H:%M')
            
            # Проверяем, есть ли уже задача в базе данных
            existing_tasks = self.db.get_tasks_for_date(today)
            task_exists = any(task['task_type'] == task_type for task in existing_tasks)
            
            if not task_exists:
                logger.warning(f"Задача {task_type} не найдена в базе данных для {today}, пропускаем проверку")
                return
            
            emoji = get_task_emoji(task_type)
            message = f"🔍 **Проверка: {task_name}**\n\n"
            message += f"⏰ {current_time}\n\n"
            message += "Выполнил ли ты эту задачу?"
            
            keyboard = [
                [InlineKeyboardButton("✅ Да, выполнил!", callback_data=f"check_yes_{task_type}_{today}")],
                [InlineKeyboardButton("❌ Нет, не успел", callback_data=f"check_no_{task_type}_{today}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.send_message_to_user(message, reply_markup)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке проверки: {e}")
    
    async def send_daily_report(self):
        """Улучшенный ежедневный отчет с мотивацией"""
        try:
            today = get_moscow_time()
            today_str = today.strftime('%Y-%m-%d')
            day_name = get_day_name(today.weekday())
            
            stats = self.db.get_completion_stats(today_str, today_str)
            tasks = self.db.get_tasks_for_date(today_str)
            
            # Заголовок с красивой датой
            report = f"📊 **Отчет за {day_name.lower()}, {today.strftime('%d.%m')}**\n\n"
            
            # Прогресс-бар
            if stats['total_tasks'] > 0:
                progress = "█" * int(stats['completion_rate'] / 10) + "░" * (10 - int(stats['completion_rate'] / 10))
                report += f"📈 Прогресс: [{progress}] {stats['completion_rate']}%\n\n"
            
            # Статистика
            report += f"✅ Выполнено: {stats['completed_tasks']}/{stats['total_tasks']}\n\n"
            
            # Детали по задачам
            if tasks:
                report += "📋 **Детали:**\n"
                for task in tasks:
                    emoji = get_task_emoji(task['task_type'])
                    status = "✅" if task['completed'] else "❌"
                    report += f"{emoji} {task['task_type']}: {status}\n"
                    if task.get('comment'):
                        report += f"   📝 {task['comment']}\n"
            
            # Мотивационное сообщение
            motivation = get_motivational_message(stats['completion_rate'])
            report += f"\n{motivation}"
            
            # Сохраняем отчет
            self.db.save_report('daily', today_str, today_str, stats)
            
            await self.send_message_to_user(report, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Ошибка при отправке ежедневного отчета: {e}")
    
    async def send_weekly_report(self):
        """Улучшенный еженедельный отчет"""
        try:
            today = get_moscow_time()
            week_start = today - datetime.timedelta(days=today.weekday())
            week_end = today
            
            week_start_str = week_start.strftime('%Y-%m-%d')
            week_end_str = week_end.strftime('%Y-%m-%d')
            
            stats = self.db.get_completion_stats(week_start_str, week_end_str)
            tasks = self.db.get_tasks_for_period(week_start_str, week_end_str)
            
            # Красивый заголовок
            report = f"📊 **Еженедельный отчет**\n"
            report += f"📅 {week_start.strftime('%d.%m')} - {week_end.strftime('%d.%m')}\n\n"
            
            # Общая статистика с прогресс-баром
            if stats['total_tasks'] > 0:
                progress = "█" * int(stats['completion_rate'] / 10) + "░" * (10 - int(stats['completion_rate'] / 10))
                report += f"📈 **Общий прогресс:** [{progress}] {stats['completion_rate']}%\n\n"
            
            report += f"✅ Выполнено: {stats['completed_tasks']}/{stats['total_tasks']}\n\n"
            
            # Статистика по дням
            daily_stats = {}
            for task in tasks:
                date = task['date']
                if date not in daily_stats:
                    daily_stats[date] = {'total': 0, 'completed': 0}
                daily_stats[date]['total'] += 1
                if task['completed']:
                    daily_stats[date]['completed'] += 1
            
            report += "📅 **По дням:**\n"
            for date in sorted(daily_stats.keys()):
                day_stats = daily_stats[date]
                rate = (day_stats['completed'] / day_stats['total'] * 100) if day_stats['total'] > 0 else 0
                date_obj = datetime.datetime.strptime(date, '%Y-%m-%d')
                day_name = get_day_name(date_obj.weekday())
                report += f"• {day_name}: {day_stats['completed']}/{day_stats['total']} ({rate:.0f}%)\n"
            
            # Комментарии за неделю
            comments = [t for t in tasks if t.get('comment')]
            if comments:
                report += "\n📝 **Комментарии:**\n"
                for t in comments:
                    date_obj = datetime.datetime.strptime(t['date'], '%Y-%m-%d')
                    day_name = get_day_name(date_obj.weekday())
                    report += f"• {day_name} {date_obj.strftime('%d.%m')} {t['task_type']}: {t['comment']}\n"

            # Мотивация
            motivation = get_motivational_message(stats['completion_rate'])
            report += f"\n{motivation}"
            
            # Сохраняем отчет
            self.db.save_report('weekly', week_start_str, week_end_str, stats)
            
            await self.send_message_to_user(report, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Ошибка при отправке еженедельного отчета: {e}")
    
    async def send_message_to_user(self, message: str, reply_markup=None, parse_mode=None):
        """Отправить сообщение пользователю"""
        pass
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная обработка нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        if update.effective_user.id != USER_ID:
            return
        
        data = query.data
        
        if data.startswith('quick_') or data.startswith('check_'):
            parts = data.split('_')
            action = parts[1]
            task_type = parts[2]
            date = parts[3]
            
            completed = action == 'yes'
            self.db.mark_task_completed(task_type, date, completed)
            
            # Улучшенная обратная связь
            emoji = get_task_emoji(task_type)
            if completed:
                response = f"{emoji} Отлично! {task_type} выполнена!\n\n💪 Продолжай в том же духе!"
                # Запросить комментарий
                context.user_data['awaiting_comment'] = {"task_type": task_type, "date": date}
                skip_keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⏭️ Пропустить", callback_data=f"skip_comment_{task_type}_{date}")]]
                )
                # Отправим отдельным сообщением, чтобы не терять мотивационный текст
                await self.send_message_to_user(
                    "📝 Хочешь оставить короткий комментарий о практике? Просто ответь текстом.",
                    reply_markup=skip_keyboard
                )
            else:
                response = f"{emoji} Понятно, {task_type} не выполнена.\n\n🌟 Завтра будет новый день!"
            
            await query.edit_message_text(response, reply_markup=None)
        elif data.startswith('skip_comment_'):
            parts = data.split('_')
            task_type = parts[2]
            date = parts[3]
            awaiting = context.user_data.get('awaiting_comment')
            if awaiting and awaiting.get('task_type') == task_type and awaiting.get('date') == date:
                context.user_data.pop('awaiting_comment', None)
            await query.edit_message_text("✅ Комментарий пропущен.")
        
        elif data.startswith('remind_later_'):
            # Обработка "напомнить позже"
            parts = data.split('_')
            task_type = parts[2]
            date = parts[3]
            
            response = f"⏰ Хорошо, напомню через 30 минут!"
            await query.edit_message_text(response, reply_markup=None)
            
            # Здесь можно добавить логику для повторного напоминания
    
    async def today_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная команда /today"""
        if update.effective_user.id != USER_ID:
            return
        
        today = get_moscow_time()
        today_str = today.strftime('%Y-%m-%d')
        day_name = get_day_name(today.weekday())
        
        # Статусы из БД по типу задачи
        tasks_in_db = {t['task_type']: t for t in self.db.get_tasks_for_date(today_str)}
        
        # Формируем список задач по расписанию на сегодня
        from config import TASKS_SCHEDULE
        scheduled_today = []
        for task_type, cfg in TASKS_SCHEDULE.items():
            if today.weekday() in cfg['days']:
                scheduled_today.append((task_type, cfg['name']))
        
        message = f"📅 **{day_name.lower()}, {today.strftime('%d.%m')}**\n\n"
        if not scheduled_today:
            message += "🎉 На сегодня задач по расписанию нет."
        else:
            message += "📋 **Твои задачи:**\n"
            for task_type, display_name in scheduled_today:
                emoji = get_task_emoji(task_type)
                if task_type in tasks_in_db:
                    status = "✅" if tasks_in_db[task_type]['completed'] else "⏳"
                else:
                    status = "⏳"
                message += f"{emoji} {display_name}: {status}\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная команда /stats"""
        if update.effective_user.id != USER_ID:
            return
        
        today = get_moscow_time().strftime('%Y-%m-%d')
        stats = self.db.get_completion_stats(today, today)
        
        message = "📊 **Твоя статистика на сегодня:**\n\n"
        
        if stats['total_tasks'] > 0:
            progress = "█" * int(stats['completion_rate'] / 10) + "░" * (10 - int(stats['completion_rate'] / 10))
            message += f"📈 Прогресс: [{progress}] {stats['completion_rate']}%\n\n"
            message += f"✅ Выполнено: {stats['completed_tasks']}/{stats['total_tasks']}\n\n"
            
            motivation = get_motivational_message(stats['completion_rate'])
            message += motivation
        else:
            message += "🎯 Пока нет данных за сегодня.\n"
            message += "Начни выполнять задачи, и статистика появится!"
        
        await update.message.reply_text(message, parse_mode='Markdown')

    async def comment_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Сохранение пользовательского комментария к задаче"""
        if update.effective_user.id != USER_ID:
            return
        awaiting = context.user_data.get('awaiting_comment')
        if not awaiting:
            return
        task_type = awaiting['task_type']
        date = awaiting['date']
        text = (update.message.text or '').strip()
        if not text:
            await update.message.reply_text("Комментарий пуст. Отправьте текст или нажмите Пропустить.")
            return
        self.db.set_task_comment(task_type, date, text)
        context.user_data.pop('awaiting_comment', None)
        await update.message.reply_text("💾 Комментарий сохранен. Спасибо!")
    
    async def start_bot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная команда /start_bot"""
        if update.effective_user.id != USER_ID:
            return
        
        if not self.scheduler.running:
            self.scheduler.start()
            message = "🤖 **Бот активирован!**\n\n"
            message += "✅ Напоминания включены\n"
            message += "📊 Отчеты будут приходить автоматически\n"
            message += "🎯 Готов помогать с задачами!"
        else:
            message = "🤖 Бот уже работает!\n\n"
            message += "Все напоминания активны."
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def stop_bot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная команда /stop_bot"""
        if update.effective_user.id != USER_ID:
            return
        
        if self.scheduler.running:
            self.scheduler.shutdown()
            message = "⏹️ **Бот приостановлен**\n\n"
            message += "🔕 Напоминания отключены\n"
            message += "📊 Отчеты не будут приходить\n\n"
            message += "💡 Используй /start_bot для возобновления"
        else:
            message = "⏹️ Бот уже приостановлен.\n\n"
            message += "💡 Используй /start_bot для запуска"
        
        await update.message.reply_text(message, parse_mode='Markdown')

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
    
    # Создаем экземпляр улучшенного бота
    bot_instance = ImprovedTaskAssistantBot()
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Переопределяем метод отправки сообщений
    async def send_message_to_user(message: str, reply_markup=None, parse_mode=None):
        try:
            await application.bot.send_message(
                chat_id=USER_ID,
                text=message,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
    
    bot_instance.send_message_to_user = send_message_to_user
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", bot_instance.start))
    application.add_handler(CommandHandler("help", bot_instance.help_command))
    application.add_handler(CommandHandler("today", bot_instance.today_command))
    application.add_handler(CommandHandler("stats", bot_instance.stats_command))
    application.add_handler(CommandHandler("start_bot", bot_instance.start_bot_command))
    application.add_handler(CommandHandler("stop_bot", bot_instance.stop_bot_command))
    
    # Добавляем обработчик кнопок
    application.add_handler(CallbackQueryHandler(bot_instance.button_callback))
    # Обработчик текстовых сообщений как комментариев
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_instance.comment_message_handler))
    
    # Запускаем планировщик
    bot_instance.scheduler.start()
    
    logger.info("Улучшенный бот запущен!")
    
    try:
        # Инициализируем приложение
        await application.initialize()
        
        # Запускаем polling
        await application.start()
        await application.updater.start_polling()
        
        logger.info("Улучшенный бот успешно запущен и работает!")
        
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
            logger.info("Начинаем остановку улучшенного бота...")
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
            logger.info("Улучшенный бот остановлен")
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

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
