# main.py  — aiogram3 + aiosend (Crypto Pay)
import os
from re import S
import time
from typing import Optional

from aiosend.types import balance
import user_context
from dotenv import load_dotenv
import fragment_api
import aiohttp
from aiogram import Bot, Dispatcher, Router, F, session
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiosend import CryptoPay
import logging

load_dotenv()

# ===== ENV =====
TOKEN = os.getenv("TOKEN")

CRYPTOPAY_TOKEN = os.getenv("CRYPTO_API")


CRYPTO_ASSET = os.getenv("CRYPTO_ASSET", "USDT")          # 'USDT' | 'TON' | ...
PRICE_PER_STAR_USD = float(os.getenv("PRICE_PER_STAR_USD", "0.02"))  # 50⭐ = $1 → 1⭐ = $0.02
RATE_UPDATE_SECONDS = int(os.getenv("RATE_UPDATE_SECONDS", "3600"))

# ===== BOT / SDK =====
bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()
cp = CryptoPay(CRYPTOPAY_TOKEN)

AMOUNTS = [50, 100, 200, 500, 1000]

# ===== Кэш курса ЦБ =====
_usd_rate: Optional[float] = None
_last_rate_ts: Optional[float] = None

# ===== Активные инвойсы =====
_active_invoices: dict = {}  # {invoice_id: {"user_id": int, "stars": int, "status": str}}

# ===== FSM для ввода своего количества =====
class BuyForm(StatesGroup):
    waiting_amount = State()

# ===== Хелперы UI =====
def create_wide_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[])

def create_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="back_to_menu")]
    ])

def create_payment_keyboard(amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Оплатить картой", callback_data=f"pay_card:{amount}"),
            InlineKeyboardButton(text="₿ Оплатить криптой", callback_data=f"pay_crypto:{amount}"),
        ],
        [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="back_to_menu")]
    ])

async def stars_menu() -> InlineKeyboardMarkup:
    rows = []
    async with aiohttp.ClientSession() as session:
        for amount in AMOUNTS:
            price_rub = await calculate_price_rub(session, amount)
            rows.append([InlineKeyboardButton(
                text=f"Купить {amount} ⭐ - {price_rub} ₽",
                callback_data=f"buy:{amount}"
            )])
    rows.append([InlineKeyboardButton(text="✏️ Ввести своё количество", callback_data="custom_amount")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ===== Курс и цены =====
async def get_usd_rate(session: aiohttp.ClientSession) -> float:
    global _usd_rate, _last_rate_ts
    now = time.time()
    if _usd_rate and _last_rate_ts and (now - _last_rate_ts) < RATE_UPDATE_SECONDS:
        return _usd_rate
    
    try:
        async with session.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=10) as resp:
            if resp.status != 200:
                return _usd_rate or 95.0
            
            # Получаем текст ответа (JavaScript формат)
            text = await resp.text()
            
            # Парсим JavaScript как JSON (убираем возможные комментарии)
            import json
            import re
            
            # Убираем комментарии и лишние символы
            clean_text = re.sub(r'//.*?\n', '\n', text)
            clean_text = re.sub(r'/\*.*?\*/', '', clean_text, flags=re.DOTALL)
            
            try:
                data = json.loads(clean_text)
            except json.JSONDecodeError:
                # Если не получилось, пробуем найти JSON в тексте
                json_match = re.search(r'\{.*\}', clean_text, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group())
                else:
                    raise ValueError("Не удалось найти JSON в ответе")
            
            if "Valute" not in data or "USD" not in data["Valute"]:
                return _usd_rate or 95.0
                
            _usd_rate = float(data["Valute"]["USD"]["Value"])
            _last_rate_ts = now
            return _usd_rate
            
    except Exception as e:
        return _usd_rate or 95.0

async def calculate_price_rub(session: aiohttp.ClientSession, stars_amount: int) -> float:
    usd_price = stars_amount * PRICE_PER_STAR_USD
    rub_price = usd_price * await get_usd_rate(session)
    return round(rub_price, 2)

import aiohttp

async def ton_price_binance(session: aiohttp.ClientSession | None = None, timeout: float = 5.0) -> float:
    url = "https://api.binance.com/api/v3/ticker/price"
    params = {"symbol": "TONUSDT"}

    own = session is None
    s = session or aiohttp.ClientSession()
    try:
        async with s.get(url, params=params, timeout=timeout) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return float(data["price"])
    finally:
        if own:
            await s.close()


# ===== Crypto Pay (создание инвойса) =====
async def create_crypto_invoice(stars: int, tg_user_id: int) -> str:
    """Создаём инвойс в Crypto Pay и возвращаем ссылку на оплату."""
    global _active_invoices
    
    usd_amount = round(stars * PRICE_PER_STAR_USD, 2)
    #usd_amount = 0.01

    # payload должен быть строкой, а не словарем
    import json
    payload_str = json.dumps({
        "tg_user_id": str(tg_user_id), 
        "stars": int(stars)
    })
    
    invoice = await cp.create_invoice(
        amount=usd_amount,
        asset=CRYPTO_ASSET,
        description=f"Telegram Stars x{stars}",
        payload=payload_str,
        expires_in=3600
    )
    
    # Сохраняем информацию об активном инвойсе
    invoice_id = invoice.invoice_id
    _active_invoices[invoice_id] = {
        "user_id": tg_user_id,
        "stars": stars,
        "status": "pending"
    }
    
    return getattr(invoice, "mini_app_invoice_url", None) or getattr(invoice, "pay_url", "")

# ===== Проверка статуса инвойсов =====
async def check_invoice_status(invoice_id: str) -> str:
    """Проверяем статус конкретного инвойса"""
    try:
        invoice = await cp.get_invoice(invoice_id)
        return invoice.status
    except Exception as e:
        logging.error(f"Ошибка проверки статуса инвойса {invoice_id}: {e}")
        return "unknown"

# ===== Обработка платежей =====
async def check_payments():
    """Проверяем статус активных инвойсов и обрабатываем подтвержденные"""
    global _active_invoices
    
    try:
        # Проверяем только активные инвойсы
        for invoice_id, invoice_data in list(_active_invoices.items()):
            if invoice_data["status"] == "pending":
                # Проверяем статус инвойса
                current_status = await check_invoice_status(invoice_id)
                
                if current_status == "paid":
                    # Платеж подтвержден - обрабатываем
                    await process_paid_invoice(invoice_id, invoice_data)
                    # Обновляем статус
                    _active_invoices[invoice_id]["status"] = "processed"
                    
                elif current_status in ["expired", "cancelled"]:
                    # Инвойс истек или отменен - удаляем из активных
                    del _active_invoices[invoice_id]
                    logging.info(f"Удален неактивный инвойс: {invoice_id} (статус: {current_status})")
                
    except Exception as e:
        logging.error(f"Ошибка при проверке платежей: {e}")

async def process_paid_invoice(invoice_id: str, invoice_data: dict):
    """Обрабатываем подтвержденный платеж"""
    try:
        tg_user_id = invoice_data["user_id"]
        stars = invoice_data["stars"]
        
        # Получаем информацию об инвойсе для отображения суммы
        try:
            invoice = await cp.get_invoice(invoice_id)
            amount = invoice.amount
            asset = invoice.asset
        except:
            amount = "0.01"  # Fallback значение
            asset = CRYPTO_ASSET

        await bot.send_message(
            tg_user_id,
            f"💫 Платёж получен!\n\n"
            f"📦 Подготавливаем к отправке {stars} ⭐...\n"
            f"Пожалуйста, подождите несколько секунд ⏳"
        )
        
        # Отправляем звезды пользователю
        await send_stars_to_user(tg_user_id, stars, invoice_id, amount, asset)
        
        # Помечаем инвойс как обработанный
        logging.info(f"Обработан платеж: {invoice_id} для пользователя {tg_user_id}, {stars} звезд")
        
    except Exception as e:
        logging.error(f"Ошибка обработки платежа {invoice_id}: {e}")

async def send_stars_to_user(tg_user_id: int, stars: int, invoice_id: str, amount: str = None, asset: str = None):
    """Отправляем звезды пользователю через Telegram Stars API"""

    try:
        u = user_context.get_user(tg_user_id) or {}
        username = (u.get("username") or str(tg_user_id)).lstrip("@")

        resp = await fragment_api.buy_stars(username, stars)

        success_message = (
            f"🎉 Платеж подтвержден!\n\n"
            f"📦 Получено: {stars} ⭐\n"
            f"💰 Сумма: {amount or 'N/A'} {asset or CRYPTO_ASSET}\n"
            f"🆔 ID транзакции: {invoice_id}\n\n"
            f"✅ Звезды зачислены на ваш аккаунт!"
        )
        
        await bot.send_message(tg_user_id, success_message)
        
    except Exception as e:
        logging.error(f"Ошибка отправки звезд пользователю {tg_user_id}: {e}")
        
        error_message = (
            f"❌ Произошла ошибка при зачислении звезд\n\n"
            f"📦 Заказано: {stars} ⭐\n"
            f"🆔 ID транзакции: {invoice_id}\n\n"
            f"🔧 Обратитесь в поддержку для решения проблемы"
        )
        
        try:
            await bot.send_message(tg_user_id, error_message)
        except:
            pass

# ===== Handlers =====
@router.message(Command("start"))
async def cmd_start(message: Message):
    kb = await stars_menu()
    user_context.get_user_ref(message)
    await message.answer(
        f"🚀 Привет!\n\n"
        "Хочешь купить Telegram Stars?\n"
        "Я помогу сделать это быстро и без лишних шагов 💫\n\n"
        "💳 Оплата по крипте или картой\n"
        "⚡ Моментальное зачисление звёзд\n"
        "🔐 Безопасно и напрямую в Telegram\n\n"
        "Выбери свой пакет ниже 👇",
        reply_markup=kb
    )

@router.callback_query(F.data == "back_to_menu")
async def cb_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = await stars_menu()
    await call.message.edit_text("Выберите пакет звёзд 👇", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery):
    amount = int(call.data.split(":")[1])
    async with aiohttp.ClientSession() as session:
        price_rub = await calculate_price_rub(session, amount)
    await call.message.edit_text(
        f"🛒 Вы выбрали пакет {amount} ⭐\n\n"
        f"💰 Стоимость: {price_rub} ₽\n\n"
        f"💳 Выберите способ оплаты:",
        reply_markup=create_payment_keyboard(amount)
    )
    await call.answer()

@router.callback_query(F.data == "custom_amount")
async def cb_custom_amount(call: CallbackQuery, state: FSMContext):
    await state.set_state(BuyForm.waiting_amount)
    await call.message.edit_text(
        "✏️ Введите количество звёзд, которое хотите купить:\n\n"
        "💡 Минимум: 50\n"
        "💡 Максимум: 10000\n\n"
        "Просто отправьте число:",
        reply_markup=create_back_keyboard()
    )
    await call.answer()

@router.message(BuyForm.waiting_amount)
async def take_custom_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if not (50 <= amount <= 10000):
            raise ValueError
    except Exception:
        await message.answer("❌ Введите корректное число от 50 до 10000.\nПопробуйте ещё раз: /start")
        await state.clear()
        return

    async with aiohttp.ClientSession() as session:
        price_rub = await calculate_price_rub(session, amount)

    await message.answer(
        f"✅ Отлично! Вы хотите купить {amount} ⭐\n\n"
        f"💰 Стоимость: {price_rub} ₽\n\n"
        f"Выберите способ оплаты:",
        reply_markup=create_payment_keyboard(amount)
    )
    await state.clear()

@router.callback_query(F.data.startswith("pay_card:"))
async def cb_pay_card(call: CallbackQuery):
    amount = int(call.data.split(":")[1])
    async with aiohttp.ClientSession() as session:
        price_rub = await calculate_price_rub(session, amount)

    await call.message.edit_text(
        f"💳 Оплата картой\n\n"
        f"📦 Пакет: {amount} ⭐\n"
        f"💰 Стоимость: {price_rub} ₽\n\n"
        f"🔗 Ссылка для оплаты будет здесь\n"
        f"⚡ После оплаты звёзды поступят автоматически",
        reply_markup=create_back_keyboard()
    )
    await call.answer()

import aiohttp

async def ton_price_binance(session: aiohttp.ClientSession, timeout: float = 5.0) -> float:
    async with session.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "TONUSDT"},
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
        return float(data["price"])

@router.callback_query(F.data.startswith("pay_crypto:"))
async def cb_pay_crypto(call: CallbackQuery):
    amount = int(call.data.split(":")[1])

    try:
        async with aiohttp.ClientSession() as session:
            ton_usdt = await ton_price_binance(session)

        wallet_raw = await fragment_api.get_balance()
        wallet_ton = float(wallet_raw)

        price_per_star = 0.75 / 50

        allow_stars = int(wallet_ton * ton_usdt / price_per_star)
        
        if amount > allow_stars:
            text = (
                "❌ Недостаточно средств на кошельке.\n\n"
                f"Доступно к покупке сейчас: {round(allow_stars, 0)} ⭐"
            )
            await call.message.edit_text(text, reply_markup=create_back_keyboard())
            await call.answer()
            return

        pay_url = await create_crypto_invoice(amount, call.from_user.id)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить в CryptoBot", url=pay_url)],
            [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="back_to_menu")]
        ])
        await call.message.edit_text(
            f"₿ Оплата криптовалютой\n\n"
            f"📦 Пакет: {amount} ⭐\n"
            f"👉 Оплатите по кнопке ниже. После подтверждения сети звёзды будут зачислены.",
            reply_markup=kb
        )
        await call.answer()

    except Exception as e:
        await call.message.edit_text(
            f"❌ Не удалось создать крипто-счёт: {e}",
            reply_markup=create_back_keyboard()
        )
        await call.answer()

# ===== Админ команды =====
@router.message(Command("check_payments"))
async def cmd_check_payments(message: Message):
    """Команда для ручной проверки платежей (только для админов)"""
    # TODO: Добавить проверку прав администратора
    if 683135069 != message.from_user.id:
        return message.answer("❌ Нет доступа к этой команде")
    await message.answer("🔍 Проверяю платежи...")
    await check_payments()
    await message.answer("✅ Проверка завершена")

@router.message(Command("clear_active"))
async def cmd_clear_active(message: Message):
    """Команда для очистки активных инвойсов (только для админов)"""
    if 683135069 != message.from_user.id:
        return message.answer("❌ Нет доступа к этой команде")

    global _active_invoices
    count = len(_active_invoices)
    _active_invoices.clear()
    await message.answer(f"🧹 Очищены активные инвойсы ({count} записей)")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда для просмотра статистики (только для админов)"""
    if 683135069 != message.from_user.id:
        return message.answer("❌ Нет доступа к этой команде")
    global _active_invoices
    pending_count = sum(1 for inv in _active_invoices.values() if inv["status"] == "pending")
    processed_count = sum(1 for inv in _active_invoices.values() if inv["status"] == "processed")
    
    await message.answer(f"📊 Статистика:\n\n"
                        f"⏳ Ожидающих платежей: {pending_count}\n"
                        f"✅ Обработанных платежей: {processed_count}\n"
                        f"📋 Всего активных инвойсов: {len(_active_invoices)}\n"
                        f"💱 Курс доллара: {_usd_rate or 'Не загружен'} ₽")

# ===== Периодические задачи =====
async def periodic_payment_check():
    """Периодическая проверка платежей каждые 30 секунд"""
    while True:
        try:
            await check_payments()
            await asyncio.sleep(30)  # Проверяем каждые 30 секунд
        except Exception as e:
            logging.error(f"Ошибка в периодической проверке платежей: {e}")
            await asyncio.sleep(60)  # При ошибке ждем минуту

# ===== entry point =====
async def main():
    """Главная функция с запуском бота и периодических задач"""
    dp.include_router(router)
    
    # Запускаем периодическую проверку платежей в фоне
    asyncio.create_task(periodic_payment_check())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
