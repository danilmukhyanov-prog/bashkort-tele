"""Telegram bot: daily exercises, language selection and persistent PostgreSQL FSM."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, BotCommand,
)
from sqlalchemy import select

from .core import Session, Base, engine, utcnow
from .models import User, BotState, Answer, Fragment
from .services import get_or_create_assignment, submit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
dp = Dispatcher()
LANGUAGES = {
    'ba-ru': '🇷🇺 Башкирский → русский',
    'ba-kk': '🇰🇿 Башкирский → казахский',
    'ba-alt': 'Башкирский → алтайский',
    'ba-ar': 'Башкирский → арабский',
    'ba-sah': 'Башкирский → якутский',
    'ba-zh': 'Башкирский → китайский',
}
MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text='📅 Задание дня'), KeyboardButton(text='🌐 Сменить язык')],
        [KeyboardButton(text='👤 Мой профиль'), KeyboardButton(text='❌ Отмена')],
    ], resize_keyboard=True,
)

def state_get(db, tid):
    return db.get(BotState, str(tid))

def state_set(db, tid, name, payload=None):
    state = state_get(db, tid)
    if state is None:
        state = BotState(telegram_id=str(tid), state=name, payload=payload or {})
        db.add(state)
    else:
        state.state, state.payload, state.updated_at = name, payload or {}, utcnow()
    db.commit()

def user_get(db, tid):
    return db.scalar(select(User).where(User.telegram_id == str(tid)))

async def offer(message: Message, user: User):
    with Session() as db:
        assignment = get_or_create_assignment(db, user)
        if assignment is None:
            await message.answer(
                'Для выбранной языковой пары задания пока не загружены.\n'
                'Администратору необходимо импортировать параллельный корпус Hugging Face.\n'
                'После загрузки попробуйте «📅 Задание дня» ещё раз.', reply_markup=MENU)
            return
        db.commit()
        if db.scalar(select(Answer.id).where(Answer.assignment_id == assignment.id)):
            await message.answer('Сегодняшнее задание уже выполнено. Возвращайтесь завтра!', reply_markup=MENU)
            return
        fragment = db.get(Fragment, assignment.fragment_id)
        state_set(db, message.from_user.id, 'choosing', {'assignment_id': assignment.id})
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='✅ Подтвердить', callback_data='confirm'),
            InlineKeyboardButton(text='✏️ Исправить', callback_data='correct'),
        ]])
        await message.answer(
            f'📖 Башкирский текст:\n{fragment.text_ba}\n\n'
            f'Предложенный перевод:\n{assignment.shown_translation}\n\n'
            'Подтвердите перевод или предложите исправление.', reply_markup=keyboard)

@dp.message(CommandStart())
async def start(message: Message):
    with Session() as db:
        user = user_get(db, message.from_user.id)
        if user is None:
            state_set(db, message.from_user.id, 'register')
            await message.answer('Һаумыһығыҙ! Введите ваше имя для регистрации:', reply_markup=MENU)
            return
    await message.answer('Добро пожаловать в Башкирскую языковую школу!', reply_markup=MENU)
    await offer(message, user)

@dp.message(Command('cancel'))
@dp.message(F.text == '❌ Отмена')
async def cancel(message: Message):
    with Session() as db:
        state_set(db, message.from_user.id, 'idle')
    await message.answer('Действие отменено. Выберите следующее действие.', reply_markup=MENU)

@dp.message(Command('daily'))
@dp.message(F.text == '📅 Задание дня')
async def daily(message: Message):
    with Session() as db:
        user = user_get(db, message.from_user.id)
    if user is None:
        await message.answer('Для регистрации отправьте /start', reply_markup=MENU)
        return
    await offer(message, user)

@dp.message(Command('language', 'lang'))
@dp.message(F.text == '🌐 Сменить язык')
async def language(message: Message):
    with Session() as db:
        user = user_get(db, message.from_user.id)
    if user is None:
        await message.answer('Для регистрации отправьте /start', reply_markup=MENU)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f'lang:{code}')]
        for code, label in LANGUAGES.items()
    ])
    await message.answer(
        f'Текущая языковая пара: {LANGUAGES.get(user.lang_pair, user.lang_pair)}\n'
        'Выберите новую языковую пару:', reply_markup=keyboard)

@dp.callback_query(F.data.startswith('lang:'))
async def change_language(cb: CallbackQuery):
    code = cb.data.split(':', 1)[1]
    if code not in LANGUAGES:
        await cb.answer('Неизвестный язык', show_alert=True)
        return
    with Session() as db:
        user = user_get(db, cb.from_user.id)
        if user is None:
            await cb.answer('Сначала отправьте /start', show_alert=True)
            return
        user.lang_pair = code
        db.commit()
        state_set(db, cb.from_user.id, 'idle')
    await cb.answer('Язык изменён')
    await cb.message.answer(
        f'Языковая пара изменена: {LANGUAGES[code]}.\n'
        'Для получения задания нажмите «📅 Задание дня».', reply_markup=MENU)

@dp.message(Command('profile'))
@dp.message(F.text == '👤 Мой профиль')
async def profile(message: Message):
    with Session() as db:
        user = user_get(db, message.from_user.id)
        if user is None:
            await message.answer('Для регистрации отправьте /start', reply_markup=MENU)
            return
        from sqlalchemy import func
        from .models import Badge
        answers = db.scalar(select(func.count(Answer.id)).where(Answer.user_id == user.id)) or 0
        badges = db.scalar(select(func.count(Badge.id)).where(Badge.user_id == user.id)) or 0
        text = (
            f'👤 {user.display_name} (ID #{user.id})\n'
            f'🌐 {LANGUAGES.get(user.lang_pair, user.lang_pair)}\n'
            f'⭐ Баллы: {user.points}\n'
            f'📈 Уровень: {1 + user.points // 100}\n'
            f'✅ Ответы: {answers}\n'
            f'🏅 Достижения: {badges}'
        )
    await message.answer(text, reply_markup=MENU)

@dp.callback_query(F.data.in_({'confirm', 'correct'}))
async def decision(cb: CallbackQuery):
    with Session() as db:
        state = state_get(db, cb.from_user.id)
        user = user_get(db, cb.from_user.id)
        if not state or state.state != 'choosing' or not user:
            await cb.answer('Задание устарело', show_alert=True)
            return
        assignment_id = state.payload['assignment_id']
        if cb.data == 'correct':
            state_set(db, cb.from_user.id, 'correcting', {'assignment_id': assignment_id})
            await cb.message.answer('Напишите корректный перевод текстом.')
            await cb.answer()
            return
        try:
            result = submit(db, user, assignment_id, 'confirm')
        except ValueError as exc:
            await cb.answer(str(exc), show_alert=True)
            return
        state_set(db, cb.from_user.id, 'idle')
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.warning('Could not clear already used Telegram keyboard', exc_info=True)
    await cb.message.answer(
        f'✅ Ответ сохранён. Начислено: {result["awarded"]}. '
        f'{"Ожидает проверки; баллы пока не начислены." if result["validation_status"] == "pending" else "Совпадает с эталоном."} '
        f'Всего баллов: {result["points"]}, уровень: {result["level"]}.',
        reply_markup=MENU)
    await cb.answer()

@dp.message(F.text)
async def text_reply(message: Message):
    tid = message.from_user.id
    with Session() as db:
        state = state_get(db, tid)
        if not state:
            await message.answer('Отправьте /start для регистрации.', reply_markup=MENU)
            return
        if state.state == 'register':
            name = message.text.strip()[:120]
            if len(name) < 2:
                await message.answer('Введите имя не короче двух символов.')
                return
            user = user_get(db, tid)
            if user is None:
                user = User(telegram_id=str(tid), display_name=name, lang_pair='ba-ru')
                db.add(user)
                db.commit()
            state_set(db, tid, 'idle')
            await message.answer('Вы зарегистрированы. Выбран башкирско-русский перевод.', reply_markup=MENU)
            await offer(message, user)
            return
        if state.state == 'correcting':
            user = user_get(db, tid)
            if user is None:
                await message.answer('Отправьте /start для регистрации.', reply_markup=MENU)
                return
            try:
                result = submit(db, user, state.payload['assignment_id'], 'correct', message.text)
            except ValueError as exc:
                await message.answer(str(exc))
                return
            state_set(db, tid, 'idle')
            await message.answer(
                f'✅ Исправление сохранено. Начислено: {result["awarded"]}. '
                f'{"Ожидает проверки; баллы пока не начислены." if result["validation_status"] == "pending" else "Совпадает с эталоном."} '
                f'Всего баллов: {result["points"]}, уровень: {result["level"]}.',
                reply_markup=MENU)
            return
    await message.answer('Выберите действие с помощью кнопок ниже.', reply_markup=MENU)

async def main():
    token = os.getenv('BOT_TOKEN')
    if not token:
        raise RuntimeError('Set BOT_TOKEN in .env')
    Base.metadata.create_all(engine)
    bot = Bot(token=token)
    await bot.set_my_commands([
        BotCommand(command='start', description='Начать / регистрация'),
        BotCommand(command='daily', description='Ежедневное задание'),
        BotCommand(command='language', description='Выбрать язык'),
        BotCommand(command='profile', description='Мой профиль'),
        BotCommand(command='cancel', description='Отменить действие'),
    ])
    logger.info('Telegram bot ready')
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
