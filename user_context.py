# user_context.py

from aiogram.types import CallbackQuery, Message

_user_refs: dict[int, dict[str, str]] = {}

def set_user(user_id: int, username: str | None, full_name: str | None = None) -> None:
    """Сохраняем пользователя в кэш."""
    _user_refs[user_id] = {
        "username": username or str(user_id),
        "full_name": full_name or username or str(user_id)
    }

def get_user(user_id: int) -> dict[str, str] | None:
    """Получаем сохранённого пользователя по ID."""
    return _user_refs.get(user_id)

def get_user_ref(event: CallbackQuery | Message) -> str:
    """
    Универсально возвращает username или user_id.
    Работает и с CallbackQuery, и с Message.
    """
    user = event.from_user
    username = user.username or str(user.id)
    full_name = user.full_name
    set_user(user.id, username, full_name)
    return username
