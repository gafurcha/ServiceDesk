import os
import uuid
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from models import Base, User, Task, Message, TaskStatus

API_TOKEN = "7705376397:AAGCDp_mnF0AHB76IxzFsnG9dyizGXjE14A"
DATABASE_URL = "sqlite:///./service_desk.db"
UPLOAD_DIR = "./static/img"

os.makedirs(UPLOAD_DIR, exist_ok=True)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(bind=engine)


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Регистрирует пользователя при выполнении команды /start"""
    telegram_id = update.effective_user.id
    first_name = update.effective_user.first_name
    last_name = update.effective_user.last_name

    with SessionLocal() as session:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            user = User(telegram_id=telegram_id, first_name=first_name, last_name=last_name)
            session.add(user)
            session.commit()
            await update.message.reply_text("Вы успешно зарегистрированы! Опишите вашу проблему")
        else:
            await update.message.reply_text("Вы уже зарегистрированы. Опишите вашу проблему")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения от пользователя"""
    telegram_id = update.effective_user.id

    with SessionLocal() as session:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            await update.message.reply_text("Пожалуйста, сначала выполните команду /start для регистрации.")
            return

        # Проверка открытых обращений пользователя
        open_task = session.query(Task).filter_by(user_id=user.id, status=TaskStatus.OPEN).first()
        process_task = session.query(Task).filter_by(user_id=user.id, status=TaskStatus.IN_PROGRESS).first()

        if not open_task and not process_task:
            open_task = Task(user_id=user.id)
            session.add(open_task)
            session.commit()
            await update.message.reply_text(
                "Ваше обращение зарегистрировано.\nОператор скоро свяжется с вами."
            )

        if open_task:
            cur_task = session.query(Task).filter_by(user_id=user.id, status=TaskStatus.OPEN).first()
        elif process_task:
            cur_task = process_task = session.query(Task).filter_by(user_id=user.id,
                                                                    status=TaskStatus.IN_PROGRESS).first()

        if update.message.text:
            new_message = Message(
                task_id=cur_task.id,
                sender=str(telegram_id),
                content=update.message.text,
            )
            session.add(new_message)
            session.commit()


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает фото сообщения от пользователя"""
    telegram_id = update.effective_user.id

    with SessionLocal() as session:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            await update.message.reply_text("Пожалуйста, сначала выполните команду /start для регистрации.")
            return

        # Проверка открытых обращений пользователя
        open_task = session.query(Task).filter_by(user_id=user.id, status=TaskStatus.OPEN).first()
        process_task = session.query(Task).filter_by(user_id=user.id, status=TaskStatus.IN_PROGRESS).first()

        if not open_task and not process_task:
            open_task = Task(user_id=user.id)
            session.add(open_task)
            session.commit()
            await update.message.reply_text(
                "Ваше обращение зарегистрировано.\nОператор скоро свяжется с вами."
            )

        if open_task:
            cur_task = session.query(Task).filter_by(user_id=user.id, status=TaskStatus.OPEN).first()
        elif process_task:
            cur_task = process_task = session.query(Task).filter_by(user_id=user.id,
                                                                    status=TaskStatus.IN_PROGRESS).first()

        if update.message.photo:
            photo = update.message.photo[-1]  # наивысшее разрешение
            file = await photo.get_file()

            file_name = f"{uuid.uuid4()}.jpg"
            file_path = os.path.join(UPLOAD_DIR, file_name)

            await file.download_to_drive(file_path)

            new_message = Message(
                task_id=cur_task.id,
                sender=str(telegram_id),
                file_name=file_name,
            )
            session.add(new_message)
            session.commit()

        else:
            await update.message.reply_text("Пришлите текст или изображение")


app = ApplicationBuilder().token(API_TOKEN).build()

# Обработчики команд
app.add_handler(CommandHandler("start", register_user))

# Обработчики сообщений
app.add_handler(MessageHandler(filters.TEXT, handle_text_message))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))

if __name__ == "__main__":
    app.run_polling()
