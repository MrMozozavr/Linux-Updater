import asyncio
import datetime
import logging
import os
import re
import shutil
import socket
import subprocess
from typing import Union

import aiohttp
import psutil
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
class IsAdminFilter(BaseFilter):
    def __init__(self, admin_id: int):
        self.admin_id = admin_id

    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        return event.from_user.id == self.admin_id


# --- Router та Стани ---
router = Router()


class ActionStates(StatesGroup):
    waiting_for_upgrade_password = State()
    waiting_for_reboot_password = State()
    waiting_for_ssh_password = State()


# --- СИСТЕМНІ ФУНКЦІЇ (HELPER) ---
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


# --- ФУНКЦІЇ МОНІТОРИНГУ ТА МЕРЕЖІ ---


def get_system_dashboard() -> str:
    """Збирає статистику: CPU, RAM, Disk, Uptime, Temp"""
    # CPU
    cpu_percent = psutil.cpu_percent(interval=1)

    # RAM
    mem = psutil.virtual_memory()
    total_mem = round(mem.total / (1024**3), 2)
    used_mem = round(mem.used / (1024**3), 2)
    free_mem = round(mem.available / (1024**3), 2)

    # DISK
    disk = psutil.disk_usage("/")
    total_disk = round(disk.total / (1024**3), 2)
    used_disk = round(disk.used / (1024**3), 2)
    disk_percent = disk.percent

    # UPTIME
    boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
    now = datetime.datetime.now()
    uptime = str(now - boot_time).split(".")[0]

    # TEMP
    temp_str = "N/A"
    try:
        temps = psutil.sensors_temperatures()
        for name in ["coretemp", "cpu_thermal", "k10temp", "acpitz", "soc_thermal"]:
            if name in temps:
                temp_str = f"{temps[name][0].current}°C"
                break
    except Exception:
        pass

    msg = (
        f"📊 <b>Стан системи:</b>\n\n"
        f"🖥 <b>Температура та загруженість процесора:</b> {cpu_percent}% (Temp: {temp_str})\n"
        f"🧠 <b>Використовування оперативної пам'яті:</b> {used_mem}GB / {total_mem}GB (Вільн: {free_mem}GB)\n"
        f"💾 <b>Кількість місця на диску (/):</b> {used_disk}GB / {total_disk}GB ({disk_percent}%)\n"
        f"⏱ <b>Час роботи системи:</b> {uptime}"
    )
    return msg


def get_failed_services() -> str:
    """Повертає список служб systemd, що впали"""
    try:
        result = subprocess.run(
            ["systemctl", "--failed", "--no-pager"], capture_output=True, text=True
        )
        if "0 loaded units listed" in result.stdout:
            return "✅ Немає служб, що впали."

        lines = result.stdout.splitlines()
        failed = []
        for line in lines:
            if "failed" in line and "loaded" in line:
                failed.append(line.strip())

        if not failed:
            return "✅ Немає критичних помилок служб."

        return "⚠️ <b>Служби, що впали:</b>\n\n" + "\n".join(failed)
    except Exception as e:
        return f"❌ Помилка: {e}"


def get_open_ports_file() -> str | None:
    """Записує відкриті порти у файл"""
    filename = "open_ports.txt"
    try:
        # Використовуємо ss без sudo. Це безпечніше.
        # Процеси (PID) можуть не відображатися без root, але порти буде видно.
        cmd = ["ss", "-tulpn"]
        with open(filename, "w") as f:
            # Пишемо заголовок
            f.write(f"Scan time: {datetime.datetime.now()}\n")
            f.write("Command: ss -tulpn\n\n")
            # Виконуємо команду і пишемо результат прямо у файл
            subprocess.run(cmd, stdout=f, text=True, check=True)
        return filename
    except Exception as e:
        logging.error(f"Помилка сканування портів: {e}")
        return None


def run_speedtest_cli() -> str:
    """Запускає speedtest-cli"""
    try:
        result = subprocess.run(
            ["speedtest-cli", "--simple"], capture_output=True, text=True, timeout=90
        )
        return f"🚀 <b>Speedtest:</b>\n\n{result.stdout}"
    except FileNotFoundError:
        return "❌ 'speedtest-cli' не встановлено. (pip install speedtest-cli)"
    except subprocess.TimeoutExpired:
        return "❌ Тайм-аут тесту швидкості."
    except Exception as e:
        return f"❌ Помилка: {e}"


async def get_external_ip() -> str:
    """Отримує зовнішній IP через API"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://ifconfig.me/ip") as resp:
                ip = await resp.text()
                return f"🌍 <b>Зовнішній IP:</b> {ip}"
    except Exception as e:
        return f"❌ Не вдалося отримати IP: {e}"


# --- ІСНУЮЧІ ФУНКЦІЇ ---
def check_system_updates() -> list[str]:
    TELEGRAM_MAX_LEN = 4000
    pm_family = get_package_manager()
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
        output = result.stdout.strip() if result.returncode in [0, 100] else ""
        if pm_family == "apt" and output.startswith("Listing..."):
            output = "\n".join(output.split("\n")[1:])

        if not output:
            return ["✅ Система оновлена."]

        full_message = f"✅ <b>Доступні оновлення:</b>\n<pre>{output}</pre>"
        if len(full_message) <= TELEGRAM_MAX_LEN:
            return [full_message]
        return [
            f"✅ Є оновлення (занадто довгий список).\nКількість рядків: {len(output.splitlines())}"
        ]
    except Exception as e:
        return [f"⚠️ Помилка перевірки оновлень: {e}"]


def run_system_upgrade(password: str) -> (bool, str):  # type: ignore
    pm_family = get_package_manager()
    upgrade_commands = {
        "pacman": ["sudo", "-S", "pacman", "-Syu", "--noconfirm"],
        "dnf": ["sudo", "-S", "dnf", "upgrade", "-y"],
        "apt": ["sudo", "-S", "bash", "-c", "apt update && apt upgrade -y"],
    }
    command = upgrade_commands.get(pm_family)
    if not command:
        return (False, "Менеджер не знайдено")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            input=password + "\n",
            check=True,
            timeout=900,
        )
        return (True, result.stdout[-2000:] or "Оновлення завершено.")
    except subprocess.CalledProcessError as e:
        if "try again" in (e.stderr or ""):
            return (False, "❌ Невірний пароль sudo!")
        return (False, f"Error:\n{e.stderr}")
    except Exception as e:
        return (False, str(e))


def reboot_system(password: str) -> (bool, str):  # type: ignore
    try:
        subprocess.run(
            ["sudo", "-S", "reboot"],
            input=password + "\n",
            check=True,
            timeout=10,
            text=True,
        )
        return (True, "Rebooting...")
    except Exception as e:
        return (False, str(e))


def manage_ssh_service(password: str, action: str) -> tuple[bool, str]:
    """
    Вмикає або вимикає SSH службу (sshd).
    action має бути 'start' або 'stop'.
    """
    if action not in ["start", "stop"]:
        return (False, "Невідома команда.")

    try:
        # Використовуємо sudo -S для передачі пароля
        # systemctl start/stop sshd
        cmd = ["sudo", "-S", "systemctl", action, "sshd"]

        subprocess.run(
            cmd,
            input=password + "\n",
            check=True,
            timeout=20,
            text=True,
            capture_output=True,
        )

        # Перевіримо статус після виконання
        status_cmd = ["systemctl", "is-active", "sshd"]
        status_res = subprocess.run(status_cmd, capture_output=True, text=True)
        current_status = status_res.stdout.strip()

        return (True, f"Команду '{action}' виконано.\nСтатус sshd: {current_status}")

    except subprocess.CalledProcessError as e:
        if "try again" in (e.stderr or ""):
            return (False, "❌ Невірний пароль sudo!")
        return (False, f"Помилка виконання:\n{e.stderr}")
    except Exception as e:
        return (False, str(e))


def get_system_logs(critical_only: bool = False, boot_offset: int = 0) -> str | None:
    boot_desc = "current" if boot_offset == 0 else "previous"
    type_desc = "critical" if critical_only else "all"
    filename = f"{type_desc}_logs_{boot_desc}_boot.txt"
    command = ["journalctl", "--no-pager", "-b", str(boot_offset)]
    if critical_only:
        command.extend(["-p", "err"])
    try:
        with open(filename, "w") as f:
            subprocess.run(command, stdout=f, text=True, check=True)
        return filename
    except Exception:
        return None


# --- ДЕТАЛІ ПРИСТРОЮ ---
async def get_device_hostname(ip: str) -> str:
    """Спроба дізнатися ім'я хоста (reverse DNS)"""
    try:
        # Запускаємо в окремому потоці, бо gethostbyaddr блокуюча
        host_info = await asyncio.to_thread(socket.gethostbyaddr, ip)
        return host_info[0]  # Повертаємо ім'я
    except Exception:
        return "Невідомо"


async def get_local_mac(ip: str) -> str:
    """Шукаємо MAC адресу в ARP таблиці (тільки для локальних)"""
    try:
        # Читаємо /proc/net/arp (стандарт Linux)
        with open("/proc/net/arp", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip:
                    return parts[3]  # MAC адреса
    except Exception:
        pass
    return ""


async def get_ip_details(ip: str) -> str:
    """Збирає всю інформацію про IP (Geo + Device Name)"""
    is_local = ip.startswith(("192.168.", "10.", "172.", "127."))

    # 1. Дізнаємося ім'я пристрою
    hostname = await get_device_hostname(ip)
    device_str = f"💻 Пристрій: <code>{hostname}</code>"

    # 2. Якщо локальний - додаємо MAC
    if is_local:
        mac = await get_local_mac(ip)
        if mac:
            device_str += f"\n🔌 MAC: <code>{mac}</code>"
        return f"🏠 Локальна мережа\n{device_str}"

    # 3. Якщо зовнішній - пробиваємо GeoIP
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://ip-api.com/json/{ip}?fields=country,city,isp,org", timeout=5
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    country = data.get("country", "Невідомо")
                    city = data.get("city", "")
                    isp = data.get("isp", data.get("org", "Невідомо"))
                    return f"🌍 {country}, {city}\n🏢 ISP: {isp}\n{device_str}"
    except Exception:
        pass

    return f"🌐 Інфо недоступне\n{device_str}"


# --- SSH МОНІТОРИНГ ---
async def monitor_ssh_logins(bot: Bot):
    logging.info("🐉 Arch Linux SSH Monitor: ЗАПУЩЕНО")
    cmd = ["journalctl", "-f", "-n", "0", "-o", "cat"]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        logging.info("✅ Процес journalctl підключено.")

        regex_login = re.compile(
            r"Accepted\s+(password|publickey)\s+for\s+(\S+)\s+from\s+(\S+)\s+port\s+(\d+)"
        )
        regex_logout = re.compile(
            r"Disconnected\s+from\s+(?:user\s+)?(\S+)\s+(\S+)\s+port\s+(\d+)"
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break

            decoded_line = line.decode("utf-8", errors="replace").strip()

            if "ssh" not in decoded_line.lower():
                continue

            # DEBUG вивід
            if (
                "Accepted" in decoded_line
                or "Disconnected" in decoded_line
                or "session closed" in decoded_line
            ):
                print(f"[DEBUG LOG]: {decoded_line}")

            # === ВХІД ===
            if "Accepted" in decoded_line:
                match = regex_login.search(decoded_line)
                if match:
                    method, user, ip, port = match.groups()

                    # Отримуємо розширену інфу про пристрій
                    geo_and_device = await get_ip_details(ip)

                    msg = (
                        f"🚨 <b>SSH: Вхід (Arch)!</b>\n"
                        f"👤 Юзер: <code>{user}</code>\n"
                        f"🔑 Метод: {method}\n"
                        f"🖥 IP: <code>{ip}</code>\n"
                        f"{geo_and_device}"
                    )
                    try:
                        await bot.send_message(ALLOWED_USER_ID, msg, parse_mode="HTML")
                    except Exception as e:
                        logging.error(f"Send Login Error: {e}")

            # === ВИХІД (Disconnected) ===
            elif "Disconnected from" in decoded_line:
                match = regex_logout.search(decoded_line)
                if match:
                    user_or_ip = match.group(1)
                    if user_or_ip.replace(".", "").isdigit():
                        user = "Невідомо (preauth)"
                        ip = user_or_ip
                    else:
                        user = user_or_ip
                        ip = match.group(2)

                    msg = (
                        f"👋 <b>SSH: Відключено</b>\n"
                        f"👤 Юзер: <code>{user}</code>\n"
                        f"🖥 IP: <code>{ip}</code>"
                    )
                    try:
                        await bot.send_message(ALLOWED_USER_ID, msg, parse_mode="HTML")
                    except Exception:
                        pass

            # === ВИХІД (PAM Session Closed) ===
            elif "session closed" in decoded_line and "user" in decoded_line:
                parts = decoded_line.split()
                if "user" in parts:
                    try:
                        user_index = parts.index("user") + 1
                        if user_index < len(parts):
                            user = parts[user_index]
                            await bot.send_message(
                                ALLOWED_USER_ID,
                                f"👋 <b>SSH: Сесію завершено</b>\n👤 Юзер: <code>{user}</code>",
                                parse_mode="HTML",
                            )
                    except Exception:
                        pass

    except Exception as e:
        logging.error(f"❌ SSH Monitor CRITICAL ERROR: {e}")


# --- КЛАВІАТУРИ ---
def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Стан системи", callback_data="sys_dashboard")
    builder.button(text="🚀 Оновити", callback_data="run_upgrade")
    builder.button(text="⚠️ Перевірка сервісів", callback_data="sys_failed")
    builder.button(text="🔄 Перевірка оновлень", callback_data="check_updates")
    builder.button(text="🌐 Мережа (IP/Ports)", callback_data="net_menu")
    builder.button(text="📄 Логи", callback_data="logs_menu")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def get_network_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Start SSH", callback_data="ssh_start")
    builder.button(text="🔴 Stop SSH", callback_data="ssh_stop")
    builder.button(text="🌍 Зовнішня IP", callback_data="net_ip")
    builder.button(text="🛡 Відкриті порти (Файл)", callback_data="net_ports")
    builder.button(text="🚀 Speedtest", callback_data="net_speed")
    builder.button(text="🔙 Назад", callback_data="menu_main")
    builder.adjust(2, 1, 1, 1, 1)
    return builder.as_markup()


def get_logs_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📄 Логи (поточні)", callback_data="get_logs_current")
    builder.button(text="🚨 Помилки (поточні)", callback_data="get_errors_current")
    builder.button(text="📄 Логи (минулі)", callback_data="get_logs_previous")
    builder.button(text="🚨 Помилки (минулі)", callback_data="get_errors_previous")
    builder.button(text="🔙 Назад", callback_data="menu_main")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


# --- ОБРОБНИКИ (HANDLERS) ---
@router.message(Command("start"))
async def send_welcome(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привіт! Бот моніторингу активний.",
        reply_markup=get_main_keyboard(),
    )


@router.callback_query(F.data == "menu_main")
async def menu_main(cb: CallbackQuery):
    await cb.message.edit_text("Головне меню:", reply_markup=get_main_keyboard())


@router.callback_query(F.data == "logs_menu")
async def menu_logs(cb: CallbackQuery):
    await cb.message.edit_text(
        "Оберіть потрібні логи:", reply_markup=get_logs_keyboard()
    )


@router.callback_query(F.data == "net_menu")
async def menu_network(cb: CallbackQuery):
    await cb.message.edit_text(
        "Мережеві інструменти:", reply_markup=get_network_keyboard()
    )


# --- DASHBOARD & SERVICES ---
@router.callback_query(F.data == "sys_dashboard")
async def show_dashboard(cb: CallbackQuery):
    msg = await asyncio.to_thread(get_system_dashboard)
    try:
        await cb.message.edit_text(
            msg, parse_mode="HTML", reply_markup=get_main_keyboard()
        )
    except Exception:
        await cb.message.answer(msg, parse_mode="HTML")


@router.callback_query(F.data == "sys_failed")
async def show_failed_services(cb: CallbackQuery):
    await cb.answer("Перевіряю сервіси...")
    msg = await asyncio.to_thread(get_failed_services)
    await cb.message.answer(msg, parse_mode="HTML")


# --- NETWORK TOOLS ---
@router.callback_query(F.data == "net_ip")
async def show_ip(cb: CallbackQuery):
    msg = await get_external_ip()
    await cb.message.answer(msg, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "net_ports")
async def show_ports(cb: CallbackQuery):
    await cb.answer("Сканую порти...")
    wait_msg = await cb.message.answer("⏳ Формую файл з портами...")

    # Запускаємо сканування у потоці
    file_path = await asyncio.to_thread(get_open_ports_file)

    await wait_msg.delete()

    if file_path and os.path.exists(file_path):
        await cb.message.answer_document(FSInputFile(file_path))
        # Видаляємо файл після відправки
        os.remove(file_path)
    else:
        await cb.message.answer(
            "❌ Не вдалося створити файл або команда 'ss' відсутня."
        )


@router.callback_query(F.data == "net_speed")
async def run_speedtest(cb: CallbackQuery):
    await cb.message.answer("⏳ Запускаю Speedtest... Це займе близько 30 сек.")
    await cb.answer()
    msg = await asyncio.to_thread(run_speedtest_cli)
    await cb.message.answer(msg, parse_mode="HTML")


# --- UPDATES & REBOOT ---
@router.callback_query(F.data == "run_upgrade")
async def process_upgrade(cb: CallbackQuery, state: FSMContext):
    await state.set_state(ActionStates.waiting_for_upgrade_password)
    await cb.message.answer("🔑 Введіть sudo пароль (повідомлення видалиться):")
    await cb.answer()


@router.message(ActionStates.waiting_for_upgrade_password)
@router.message(ActionStates.waiting_for_reboot_password)
@router.message(ActionStates.waiting_for_ssh_password)
async def handle_password(message: Message, state: FSMContext):
    if not message.text:
        return
    password = message.text

    try:
        await message.delete()
    except Exception:
        pass

    wait_msg = await message.answer("⏳ Пароль прийнято, виконую...")
    current_state = await state.get_state()

    if current_state == ActionStates.waiting_for_upgrade_password:
        success, output = await asyncio.to_thread(run_system_upgrade, password)
        await wait_msg.delete()
        if success:
            builder = InlineKeyboardBuilder()
            builder.button(text="Так, Reboot", callback_data="reboot_yes")
            builder.button(text="Ні", callback_data="reboot_no")
            await message.answer(
                "✅ Оновлено!\nПерезавантажити?", reply_markup=builder.as_markup()
            )
        else:
            await message.answer(f"❌ Помилка:\n{output}")

    elif current_state == ActionStates.waiting_for_reboot_password:
        success, output = await asyncio.to_thread(reboot_system, password)
        await wait_msg.delete()
        if not success:
            await message.answer(f"❌ Fail:\n{output}")

    elif current_state == ActionStates.waiting_for_ssh_password:
        # Отримуємо дію (start/stop), яку ми зберегли раніше
        data = await state.get_data()
        action = data.get("ssh_action", "start")

        success, output = await asyncio.to_thread(manage_ssh_service, password, action)
        await wait_msg.delete()

        if success:
            await message.answer(f"✅ Успішно:\n{output}")
        else:
            await message.answer(f"❌ Помилка:\n{output}")

    await state.clear()


@router.callback_query(F.data == "reboot_yes")
async def reboot_confirm(cb: CallbackQuery, state: FSMContext):
    await state.set_state(ActionStates.waiting_for_reboot_password)
    await cb.message.answer("🔑 Введіть sudo пароль для перезавантаження:")
    await cb.answer()


@router.callback_query(F.data == "reboot_no")
async def reboot_cancel(cb: CallbackQuery):
    await cb.message.delete()


@router.callback_query(F.data == "check_updates")
async def check_updates_handler(cb: CallbackQuery):
    await cb.answer("Перевірка...")
    chunks = await asyncio.to_thread(check_system_updates)
    for chunk in chunks:
        await cb.message.answer(chunk, parse_mode="HTML")


# --- LOGS HANDLERS ---
@router.callback_query(F.data.startswith("get_"))
async def process_get_logs(cb: CallbackQuery):
    data = cb.data
    is_critical = "errors" in data
    is_previous = "previous" in data
    boot_offset = -1 if is_previous else 0

    wait = await cb.message.answer("⏳ Експорт логів...")
    log_file = await asyncio.to_thread(get_system_logs, is_critical, boot_offset)
    await wait.delete()

    if log_file and os.path.exists(log_file):
        await cb.message.answer_document(FSInputFile(log_file))
        os.remove(log_file)
    else:
        await cb.message.answer("❌ Файл пустий або помилка.")


@router.callback_query(F.data.in_({"ssh_start", "ssh_stop"}))
async def process_ssh_manage(cb: CallbackQuery, state: FSMContext):
    action = "start" if cb.data == "ssh_start" else "stop"

    # Запам'ятовуємо, яку дію ми хочемо зробити (start чи stop)
    await state.update_data(ssh_action=action)
    await state.set_state(ActionStates.waiting_for_ssh_password)

    action_text = "УВІМКНУТИ" if action == "start" else "ВИМКНУТИ"
    warning = (
        "\n⚠️ Увага: Якщо ви підключені по SSH, з'єднання розірветься!"
        if action == "stop"
        else ""
    )

    await cb.message.answer(
        f"🔑 Ви хочете <b>{action_text}</b> SSH.{warning}\n"
        "Введіть sudo пароль (повідомлення видалиться):",
        parse_mode="HTML",
    )
    await cb.answer()


# --- MAIN ---
async def main():
    dp = Dispatcher(storage=MemoryStorage())

    router.message.filter(IsAdminFilter(ALLOWED_USER_ID))
    router.callback_query.filter(IsAdminFilter(ALLOWED_USER_ID))
    dp.include_router(router)

    bot = Bot(token=API_TOKEN)

    async def on_startup():
        await bot.delete_webhook(drop_pending_updates=True)
        try:
            distro = get_distro_pretty_name()
            await bot.send_message(
                ALLOWED_USER_ID,
                f"🚀 Ваш помічник в системі {distro} запущений!",
                reply_markup=get_main_keyboard(),
            )
        except Exception as e:
            logging.error(f"Запуск не вдався: {e}")

        asyncio.create_task(monitor_ssh_logins(bot))

    dp.startup.register(on_startup)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
