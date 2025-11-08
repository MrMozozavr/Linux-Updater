import asyncio
import logging
import os
import shutil
import subprocess
from typing import Union

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
load_dotenv()
API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

if not API_TOKEN or not ALLOWED_USER_ID:
    raise ValueError(
        "Помилка: Не вдалося завантажити API_TOKEN або ALLOWED_USER_ID з .env файлу."
    )


# --- ФІЛЬТР БЕЗПЕКИ ---
# Цей клас буде перевіряти, чи є користувач адміністратором
class IsAdminFilter(BaseFilter):
    def __init__(self, admin_id: int):
        self.admin_id = admin_id

    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        # Перевіряємо ID користувача. Спрацює і для повідомлень, і для натискань кнопок.
        return event.from_user.id == self.admin_id


# --- Router та Стани ---
router = Router()


class ActionStates(StatesGroup):
    waiting_for_upgrade_password = State()
    waiting_for_reboot_password = State()


# --- СИСТЕМНІ ФУНКЦІЇ (без змін) ---
def get_package_manager() -> str | None:
    managers = ["pacman", "dnf", "apt"]
    for m in managers:
        if shutil.which(m):
            return m
    return None


def get_distro_pretty_name() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=")[1].strip().strip('"')
    except FileNotFoundError:
        return "Linux"
    return "Linux"


def check_system_updates() -> list[str]:
    TELEGRAM_MAX_LEN = 4000
    pm_family = get_package_manager()
    distro_name = get_distro_pretty_name()
    if not pm_family:
        return ["⚠️ Помилка: Не вдалося визначити пакетний менеджер."]
    distro_commands = {
        "pacman": ["checkupdates"],
        "dnf": ["dnf", "check-update"],
        "apt": ["apt", "list", "--upgradable"],
    }
    command = distro_commands.get(pm_family)
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        output = ""
        if pm_family == "pacman":
            if result.returncode == 0:
                output = result.stdout
            elif result.returncode == 2:
                output = ""
            else:
                error_details = result.stderr or "Немає деталей."
                raise subprocess.CalledProcessError(
                    result.returncode, command, stderr=error_details
                )
        elif pm_family == "dnf":
            if result.returncode == 100:
                output = result.stdout
            elif result.returncode == 0:
                output = ""
            else:
                error_details = (
                    result.stderr or result.stdout or "Невідома помилка DNF."
                )
                raise subprocess.CalledProcessError(
                    result.returncode, command, stderr=error_details
                )
        elif pm_family == "apt":
            if result.returncode == 0:
                output = result.stdout
            else:
                error_details = (
                    result.stderr or result.stdout or "Невідома помилка APT."
                )
                raise subprocess.CalledProcessError(
                    result.returncode, command, stderr=error_details
                )
        output = output.strip()
        if pm_family == "apt" and output.startswith("Listing..."):
            output = "\n".join(output.split("\n")[1:])
        if not output.strip():
            return [f"✅ Система ({distro_name}) оновлена. Нових пакетів немає."]
        header = f"✅ Доступні оновлення для {distro_name}:\n\n"
        full_message = header + "```\n" + output + "\n```"
        if len(full_message) <= TELEGRAM_MAX_LEN:
            return [full_message]
        messages = []
        lines = output.strip().split("\n")
        current_chunk = header + "```\n"
        for line in lines:
            if len(current_chunk) + len(line) + 4 > TELEGRAM_MAX_LEN:
                current_chunk += "```"
                messages.append(current_chunk)
                current_chunk = "```\n"
            current_chunk += line + "\n"
        current_chunk += "```"
        messages.append(current_chunk)
        return messages
    except FileNotFoundError:
        return [f"⚠️ Помилка: команда '{command[0]}' не знайдена."]
    except subprocess.CalledProcessError as e:
        return [f"⚠️ Помилка під час перевірки оновлень:\n{e.stderr}"]


def run_system_upgrade(password: str) -> (bool, str): # type: ignore
    pm_family = get_package_manager()
    if not pm_family:
        return (False, "Не вдалося визначити пакетний менеджер.")
    upgrade_commands = {
        "pacman": ["sudo", "-S", "pacman", "-Syu", "--noconfirm"],
        "dnf": ["sudo", "-S", "dnf", "upgrade", "-y"],
        "apt": ["sudo", "-S", "bash", "-c", "apt update && apt upgrade -y"],
    }
    command = upgrade_commands.get(pm_family)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            input=password + "\n",
            check=True,
            timeout=900,
        )
        return (True, result.stdout or "Оновлення успішно завершено.")
    except subprocess.TimeoutExpired:
        return (False, "❌ Помилка: Час очікування оновлення вичерпано.")
    except subprocess.CalledProcessError as e:
        error_output = e.stderr or e.stdout
        if "Sorry, try again" in error_output:
            return (False, "❌ Невірний пароль sudo!")
        error_message = f"STDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}"
        return (False, error_message)


def reboot_system(password: str) -> (bool, str): # type: ignore
    try:
        subprocess.run(
            ["sudo", "-S", "reboot"],
            capture_output=True,
            text=True,
            input=password + "\n",
            check=True,
            timeout=60,
        )
        return (True, "Команда на перезавантаження відправлена.")
    except subprocess.TimeoutExpired:
        return (
            False,
            "❌ Помилка: Час очікування команди перезавантаження вичерпано.",
        )
    except subprocess.CalledProcessError as e:
        if "Sorry, try again" in e.stderr:
            return (False, "❌ Невірний пароль sudo!")
        return (False, f"Помилка при перезавантаженні:\n{e.stderr}")


def get_system_logs(critical_only: bool = False, boot_offset: int = 0) -> str | None:
    boot_desc = "current" if boot_offset == 0 else "previous"
    type_desc = "critical" if critical_only else "all"
    filename = f"{type_desc}_logs_{boot_desc}_boot.txt"
    command = ["journalctl", "--no-pager"]
    if critical_only:
        command.extend(["-p", "err"])
    command.extend(["-b", str(boot_offset)])
    try:
        with open(filename, "w") as f:
            subprocess.run(command, stdout=f, text=True, check=True)
        return filename
    except Exception as e:
        logging.error(f"Помилка при отриманні логів: {e}")
        return None


# --- КЛАВІАТУРА ---
def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Оновити систему", callback_data="run_upgrade")
    builder.button(text="🔄 Перевірити оновлення", callback_data="check_updates")
    builder.button(text="📄 Логи (поточне)", callback_data="get_logs_current")
    builder.button(text="🚨 Помилки (поточне)", callback_data="get_errors_current")
    builder.button(text="📄 Логи (минуле)", callback_data="get_logs_previous")
    builder.button(text="🚨 Помилки (минуле)", callback_data="get_errors_previous")
    builder.adjust(1, 1, 2, 2)
    return builder.as_markup()


# --- ОБРОБНИКИ (HANDLERS) ---
# Всі обробники тепер захищені фільтром IsAdminFilter
@router.message(Command("start"))
async def send_welcome(message: types.Message, state: FSMContext):
    await state.clear()
    distro_name = get_distro_pretty_name()
    await message.answer(
        f"👋 Привіт, {message.from_user.full_name}!\nЯ бот для моніторингу твоєї системи {distro_name}.",
        reply_markup=get_main_keyboard(),
    )


@router.message(ActionStates.waiting_for_upgrade_password)
@router.message(ActionStates.waiting_for_reboot_password)
async def handle_password(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Будь ласка, надішліть пароль у вигляді тексту.")
        return
    password = message.text
    current_state = await state.get_state()
    await message.delete()
    if current_state == ActionStates.waiting_for_upgrade_password:
        await message.answer("⏳ Пароль отримано. Починаю оновлення...")
        success, output = await asyncio.to_thread(run_system_upgrade, password)
        if success:
            await message.answer(
                "✅ Систему успішно оновлено!", parse_mode="Markdown"
            )
            builder = InlineKeyboardBuilder()
            builder.button(text="Так, перезавантажити", callback_data="reboot_yes")
            builder.button(text="Ні, пізніше", callback_data="reboot_no")
            await message.answer(
                "🔄 Бажаєте перезавантажити систему зараз?",
                reply_markup=builder.as_markup(),
            )
        else:
            await message.answer(
                f"❌ Помилка під час оновлення!\n\n{output}",
                parse_mode="Markdown",
            )
    elif current_state == ActionStates.waiting_for_reboot_password:
        await message.answer(
            "⏳ Пароль отримано. Відправляю команду на перезавантаження..."
        )
        success, output = await asyncio.to_thread(reboot_system, password)
        if not success:
            await message.answer(
                f"❌ Не вдалося перезавантажити!\n\n{output}",
                parse_mode="Markdown",
            )
    await state.clear()


# Обробник невідомих команд тепер не потрібен, бо фільтр їх відкине.


@router.callback_query(F.data == "run_upgrade")
async def process_system_upgrade_request(
    callback_query: types.CallbackQuery, state: FSMContext
):
    await state.set_state(ActionStates.waiting_for_upgrade_password)
    await callback_query.message.answer(
        "🔑 Для оновлення, будь ласка, надішліть ваш sudo пароль.\n\nПовідомлення буде видалено."
    )
    await callback_query.answer()


@router.callback_query(F.data == "reboot_yes")
async def process_reboot_request(
    callback_query: types.CallbackQuery, state: FSMContext
):
    await state.set_state(ActionStates.waiting_for_reboot_password)
    await callback_query.message.edit_text(
        "🔑 Для перезавантаження, будь ласка, надішліть ваш sudo пароль.\n\nПовідомлення буде видалено.",
        reply_markup=None,
    )
    await callback_query.answer()


@router.callback_query(F.data == "reboot_no")
async def process_reboot_no(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.message.edit_text(
        "✅ Добре, перезавантаження скасовано.", reply_markup=None
    )
    await callback_query.answer()


@router.callback_query(F.data == "check_updates")
async def process_check_updates(callback_query: types.CallbackQuery):
    await callback_query.message.answer("⏳ Перевіряю оновлення...")
    await callback_query.answer()
    message_chunks = await asyncio.to_thread(check_system_updates)
    for chunk in message_chunks:
        await callback_query.message.answer(chunk, parse_mode="Markdown")
        await asyncio.sleep(0.5)


@router.callback_query(F.data.startswith("get_"))
async def process_get_logs(callback_query: types.CallbackQuery):
    data = callback_query.data
    is_critical = "errors" in data
    is_previous = "previous" in data
    boot_offset = -1 if is_previous else 0
    log_type = "критичних помилок" if is_critical else "системних логів"
    period = "минулого" if is_previous else "поточного"
    await callback_query.message.answer(
        f"⏳ Готую файл з архівом {log_type} за {period} завантаження..."
    )
    await callback_query.answer()
    log_file = await asyncio.to_thread(
        get_system_logs, critical_only=is_critical, boot_offset=boot_offset
    )
    if log_file and os.path.exists(log_file):
        document = FSInputFile(log_file)
        await callback_query.message.answer_document(document)
        os.remove(log_file)
    else:
        await callback_query.message.answer(
            "⚠️ Помилка: Не вдалося створити файл з логами."
        )


# --- ГОЛОВНА ФУНКЦІЯ ЗАПУСКУ ---
async def main():
    dp = Dispatcher(storage=MemoryStorage())

    # === ДОДАЄМО ЗАХИСТ ===
    # Застосовуємо наш фільтр до всіх повідомлень та натискань кнопок
    router.message.filter(IsAdminFilter(ALLOWED_USER_ID))
    router.callback_query.filter(IsAdminFilter(ALLOWED_USER_ID))

    dp.include_router(router)
    bot = Bot(token=API_TOKEN)

    async def on_startup():
        try:
            distro_name = get_distro_pretty_name()
            await bot.send_message(
                ALLOWED_USER_ID,
                f"🚀 Бот для моніторингу {distro_name} запущений!",
                reply_markup=get_main_keyboard(),
            )
        except Exception as e:
            logging.error(f"Не вдалося відправити стартове повідомлення: {e}")

    dp.startup.register(on_startup)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
