import logging
import datetime
from typing import Dict, List
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import BOT_TOKEN, USER_ID, TIMEZONE, TASKS_SCHEDULE
from database import TaskDatabase

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TaskAssistantBot:
    def __init__(self):
        self.db = TaskDatabase()
        self.scheduler = AsyncIOScheduler(timezone=TIMEZONE)
        self.setup_scheduler()
    
    def setup_scheduler(self):
        """Настройка расписания напоминаний"""
        for task_type, task_config in TASKS_SCHEDULE.items():
            # Напоминание о задаче
            for day in task_config['days']:
                hour, minute = map(int, task_config['time'].split(':'))
                self.scheduler.add_job(
                    self.send_task_reminder,
                    CronTrigger(day_of_week=day, hour=hour, minute=minute),
                    args=[task_type, task_config['name']],
                    id=f'reminder_{task_type}_{day}'
                )
            
            # Контроль выполнения
            for day in task_config['days']:
                hour, minute = map(int, task_config['check_time'].split(':'))
                self.scheduler.add_job(
                    self.send_completion_check,
                    CronTrigger(day_of_week=day, hour=hour, minute=minute),
                    args=[task_type, task_config['name']],
                    id=f'check_{task_type}_{day}'
                )
        
        # Ежедневный отчет в 22:00
        self.scheduler.add_job(
            self.send_daily_report,
            CronTrigger(hour=22, minute=0),
            id='daily_report'
        )
        
        # Еженедельный отчет в воскресенье в 22:30
        self.scheduler.add_job(
            self.send_weekly_report,
            CronTrigger(day_of_week=6, hour=22, minute=30),  # 6 = воскресенье
            id='weekly_report'
        )
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        if update.effective_user.id != USER_ID:
            await update.message.reply_text("Извините, этот бот предназначен только для определенного пользователя.")
            return
        
        welcome_text = """
🤖 Добро пожаловать в ваш персональный ассистент задач!

Я буду напоминать вам о ваших ежедневных задачах и контролировать их выполнение.

📋 Ваши задачи:
• 06:05 - Медитация (контроль в 06:50)
• 09:00 - Планирование (контроль в 09:16)
• 15:00 - Тренировка (Пн, Чт, Вс) или Йога (Вт, Ср, Пт, Сб) (контроль в 17:00)

📊 Отчеты:
• Ежедневный отчет в 22:00
• Еженедельный отчет в воскресенье в 22:30

Используйте /help для получения списка команд.
        """
        await update.message.reply_text(welcome_text)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help"""
        if update.effective_user.id != USER_ID:
            return
        
        help_text = """
📖 Доступные команды:

/start - Начать работу с ботом
/help - Показать это сообщение
/today - Показать задачи на сегодня
/week - Показать задачи на неделю
/stats - Показать статистику выполнения
/report - Получить отчет за сегодня
/status - Показать статус бота

🔧 Управление:
/start_bot - Запустить напоминания
/stop_bot - Остановить напоминания
        """
        await update.message.reply_text(help_text)
    
    async def send_task_reminder(self, task_type: str, task_name: str):
        """Отправить напоминание о задаче"""
        try:
            today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
            
            # Добавляем задачу в базу данных
            task_id = self.db.add_task(task_type, today)
            
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
            
            # Отмечаем, что напоминание отправлено
            self.db.mark_reminder_sent(task_id)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания: {e}")
    
    async def send_completion_check(self, task_type: str, task_name: str):
        """Отправить проверку выполнения задачи"""
        try:
            today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
            
            message = f"🔍 Контроль выполнения!\n\n📋 Задача: {task_name}\n⏰ Время проверки: {datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M')}\n\nВыполнили ли вы эту задачу?"
            
            keyboard = [
                [InlineKeyboardButton("✅ Да, выполнил", callback_data=f"check_yes_{task_type}_{today}")],
                [InlineKeyboardButton("❌ Нет, не выполнил", callback_data=f"check_no_{task_type}_{today}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.send_message_to_user(message, reply_markup)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке проверки: {e}")
    
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
            
            # Сохраняем отчет
            self.db.save_report('daily', today, today, stats)
            
            await self.send_message_to_user(report)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке ежедневного отчета: {e}")
    
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
            
            # Сохраняем отчет
            self.db.save_report('weekly', week_start, week_end, stats)
            
            await self.send_message_to_user(report)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке еженедельного отчета: {e}")
    
    async def send_message_to_user(self, message: str, reply_markup=None):
        """Отправить сообщение пользователю"""
        # Этот метод будет переопределен в main функции
        pass
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        if update.effective_user.id != USER_ID:
            return
        
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
    
    async def today_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /today"""
        if update.effective_user.id != USER_ID:
            return
        
        tz = pytz.timezone(TIMEZONE)
        now = datetime.datetime.now(tz)
        today_str = now.strftime('%Y-%m-%d')
        weekday = now.weekday()  # 0=понедельник
        
        # Статусы из БД по типу задачи
        tasks_in_db = {t['task_type']: t for t in self.db.get_tasks_for_date(today_str)}
        
        # Формируем список задач по расписанию на сегодня
        scheduled_today = []
        for task_type, cfg in TASKS_SCHEDULE.items():
            if weekday in cfg['days']:
                scheduled_today.append((task_type, cfg['name']))
        
        # Если по расписанию ничего нет (теоретически), сообщим, но с нашим ТЗ это не случится
        if not scheduled_today:
            await update.message.reply_text(f"📅 На сегодня ({today_str}) задач нет по расписанию.")
            return
        
        message = f"📋 Задачи на сегодня ({today_str}):\n\n"
        for task_type, display_name in scheduled_today:
            if task_type in tasks_in_db:
                status = "✅" if tasks_in_db[task_type]['completed'] else "⏳"
            else:
                status = "⏳"  # еще нет записи в БД, но задача по расписанию есть
            message += f"• {display_name}: {status}\n"
        
        await update.message.reply_text(message)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stats"""
        if update.effective_user.id != USER_ID:
            return
        
        today = datetime.datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
        stats = self.db.get_completion_stats(today, today)
        
        message = f"📊 Статистика на сегодня:\n\n"
        message += f"• Всего задач: {stats['total_tasks']}\n"
        message += f"• Выполнено: {stats['completed_tasks']}\n"
        message += f"• Процент выполнения: {stats['completion_rate']}%"
        
        await update.message.reply_text(message)
    
    async def start_bot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start_bot"""
        if update.effective_user.id != USER_ID:
            return
        
        if not self.scheduler.running:
            self.scheduler.start()
            await update.message.reply_text("🤖 Бот запущен! Напоминания активированы.")
        else:
            await update.message.reply_text("🤖 Бот уже запущен.")
    
    async def stop_bot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stop_bot"""
        if update.effective_user.id != USER_ID:
            return
        
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
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", bot_instance.start))
    application.add_handler(CommandHandler("help", bot_instance.help_command))
    application.add_handler(CommandHandler("today", bot_instance.today_command))
    application.add_handler(CommandHandler("stats", bot_instance.stats_command))
    application.add_handler(CommandHandler("start_bot", bot_instance.start_bot_command))
    application.add_handler(CommandHandler("stop_bot", bot_instance.stop_bot_command))
    
    # Добавляем обработчик кнопок
    application.add_handler(CallbackQueryHandler(bot_instance.button_callback))
    
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
        
        # Регистрируем обработчик сигналов
        for sig in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(sig, lambda s, f: signal_handler())
        
        # Ждем бесконечно
        while True:
            await asyncio.sleep(1)
            
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
