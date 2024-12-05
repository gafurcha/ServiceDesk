import os
import uuid
from fastapi import FastAPI, HTTPException, Depends, Path, File, UploadFile, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, joinedload, Session
from pydantic import BaseModel
from typing import List, Optional
from models import User, Task, Message, Manager, TaskStatus
from datetime import datetime
from telegram import Bot
from sqladmin import Admin, ModelView
import uvicorn

DATABASE_URL = "sqlite:///./service_desk.db"
engine = create_engine(DATABASE_URL, echo=True)
SessionLocal = sessionmaker(bind=engine)

TELEGRAM_BOT_TOKEN = "7705376397:AAGCDp_mnF0AHB76IxzFsnG9dyizGXjE14A"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

app = FastAPI()
app.mount("/static", StaticFiles(directory="./static/img"), name="static")

templates = Jinja2Templates(directory="./templates")

admin = Admin(app, engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class UserCreate(BaseModel):
    telegram_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class TaskCreate(BaseModel):
    user_id: int
    manager_id: Optional[int] = None
    status: TaskStatus = TaskStatus.OPEN


class MessageCreate(BaseModel):
    task_id: int
    sender: str
    content: Optional[str] = None
    file_name: Optional[str] = None
    operator_id: int
    timestamp: datetime


@app.get("/users", response_model=List[UserCreate])
def get_users(db: Session = Depends(get_db)):
    result = db.execute(select(User))
    return result.scalars().unique().all()


@app.post("/users", response_model=UserCreate)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = User(telegram_id=user.telegram_id, first_name=user.first_name, last_name=user.last_name)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


@app.get("/tasks", response_model=List[TaskCreate])
def get_tasks(db: Session = Depends(get_db)):
    result = db.execute(select(Task))
    return result.scalars().unique().all()


@app.post("/tasks", response_model=TaskCreate)
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    db_task = Task(user_id=task.user_id, manager_id=task.manager_id, status=task.status)
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@app.get("/messages", response_model=List[MessageCreate])
def get_messages_by_task(
        task_id: int,
        db: Session = Depends(get_db),
):
    result = db.execute(select(Message).filter(Message.task_id == task_id))
    messages = result.scalars().unique().all()

    if not messages:
        raise HTTPException(status_code=404, detail=f"Сообщения для обращения с {task_id} не найдены")

    return messages


@app.post("/messages", response_model=MessageCreate)
def create_message(message: MessageCreate, file: UploadFile = File(None), db: Session = Depends(get_db)):
    if file:
        file_name = f"{uuid.uuid4()}.jpg"
        file_path = os.path.join("static", "img", file_name)

        os.makedirs(os.path.dirname(file_path), exist_ok=True)  # проверка на папку с изображением
        with open(file_path, "wb") as buffer:
            buffer.write(file.file.read())

        db_message = Message(
            task_id=message.task_id,
            sender=message.sender,
            file_name=file_name
        )
    else:
        db_message = Message(
            task_id=message.task_id,
            sender=message.sender,
            content=message.content
        )

    db.add(db_message)
    db.commit()
    db.refresh(db_message)

    return db_message


@app.get("/operator/tasks", response_class=HTMLResponse)
def operator_view_tasks(request: Request, db: Session = Depends(get_db)):
    result = db.execute(select(Task).options(joinedload(Task.messages)))
    tasks = result.unique().scalars().all()

    managers_result = db.execute(select(Manager))
    managers = managers_result.scalars().all()

    return templates.TemplateResponse("tasks.html", {"request": request, "tasks": tasks, "managers": managers})


@app.get("/operator/tasks/{task_id}", response_class=HTMLResponse)
def get_task_detail(request: Request, task_id: int, db: Session = Depends(get_db)):
    result = db.execute(select(Task).filter(Task.id == task_id).options(joinedload(Task.messages)))
    task = result.scalars().first()

    if not task:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    return templates.TemplateResponse("task_detail.html", {"request": request, "task": task})


@app.post("/operator/tasks/{task_id}/assign")
def assign_manager_to_task(
        task_id: int,
        manager_id: int = Form(...),
        db: Session = Depends(get_db)):
    result = db.execute(select(Task).filter(Task.id == task_id))
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    task.manager_id = manager_id
    task.status = TaskStatus.IN_PROGRESS
    db.commit()

    return {"message": f"Обращение {task_id} теперь в работе и назначен менеджер {manager_id}."}


@app.post("/operator/tasks/{task_id}/status")
def update_task_status(
        task_id: int,
        status: TaskStatus = Form(...),
        db: Session = Depends(get_db)):
    result = db.execute(select(Task).filter(Task.id == task_id))
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    task.status = status

    if status == TaskStatus.CLOSED:
        task.close_date = datetime.utcnow()

    db.commit()

    return {"message": f"Обращение {task_id} обновлено на статус {status}."}


@app.post("/operator/tasks/{task_id}/reply")
async def reply_to_task(
        task_id: int,
        content: str = Form(...),
        operator_id: int = Form(...),
        image: Optional[UploadFile] = Form(None),
        db: Session = Depends(get_db)):
    result = db.execute(select(Task).filter(Task.id == task_id))
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    file_name = None
    file_path = None

    if image and image.filename:
        file_name = f"{uuid.uuid4()}.jpg"
        file_path = os.path.join("static", "img", file_name)

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        image_content = await image.read()
        if image_content:
            with open(file_path, "wb") as buffer:
                buffer.write(image_content)
        else:
            file_name = None

    db_message = Message(task_id=task_id, sender="manager", content=content, operator_id=operator_id,
                         file_name=file_name)
    db.add(db_message)
    db.commit()

    user_result = db.execute(select(User).filter(User.id == task.user_id))
    user = user_result.scalar_one_or_none()

    if user:
        if content:
            await bot.send_message(chat_id=user.telegram_id, text=content)

        if file_name:
            await bot.send_photo(chat_id=user.telegram_id, photo=open(file_path, 'rb'))

    return {"message": "Ответ успешно отправлен."}


class UserAdmin(ModelView, model=User):
    column_list = [User.telegram_id, User.first_name, User.last_name]


class TaskAdmin(ModelView, model=Task):
    column_list = [Task.id, Task.user_id, Task.manager_id, Task.status, Task.close_date]


class MessageAdmin(ModelView, model=Message):
    column_list = [Message.task_id, Message.sender, Message.content, Message.file_name, Message.timestamp]


class ManagerAdmin(ModelView, model=Manager):
    column_list = [Manager.id, Manager.first_name, Manager.last_name]


admin.add_view(UserAdmin)
admin.add_view(TaskAdmin)
admin.add_view(MessageAdmin)
admin.add_view(ManagerAdmin)

if __name__ == "__main__":
    uvicorn.run("fast:app", host="0.0.0.0", port=8000, reload=True)