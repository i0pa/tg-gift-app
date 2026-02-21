import asyncio
import logging
import json
import os
import sys
from aiogram import Bot, Dispatcher, Router, types
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

# ──────────────────────────────────────────────
#  FSM States
# ──────────────────────────────────────────────
class AdminStates(StatesGroup):
    waiting_for_channel_link = State()
    waiting_for_private_channel_id = State()
    waiting_for_reward_link = State()
    waiting_for_broadcast_message = State()
    waiting_for_post_message = State()
    waiting_for_post_channel_selection = State()

# ──────────────────────────────────────────────
#  Config  — замените на свои значения
# ──────────────────────────────────────────────
BOT_TOKEN  = "7606407600:AAEVWdrKEYnHmPYgDu7OUeBn_IWCEvG4bjk"
ADMIN_ID   = 1638434122
DATA_FILE  = "bot_data.json"

# URL вашего Mini Web App (GitHub Pages или другой хостинг)
# Пример: "https://username.github.io/my-repo/index.html"
MINI_APP_URL = "https://YOUR-GITHUB-USERNAME.github.io/YOUR-REPO/index.html"

# ──────────────────────────────────────────────
#  State
# ──────────────────────────────────────────────
CHANNELS = []
STATS    = {"users_started": 0, "users_subscribed": 0}
USERS    = set()
REWARD_LINK  = "https://your-reward-link-here.com"
SUCCESS_MESSAGE = f"Вы выполнили все условия! Получите материал: {REWARD_LINK}"

# ──────────────────────────────────────────────
#  Persistence
# ──────────────────────────────────────────────
def load_data():
    global CHANNELS, STATS, USERS, REWARD_LINK, SUCCESS_MESSAGE
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                CHANNELS = [tuple(ch) for ch in data.get("channels", [])]
                STATS    = data.get("stats", {"users_started": 0, "users_subscribed": 0})
                USERS    = set(data.get("users", []))
                REWARD_LINK = data.get("reward_link", REWARD_LINK)
                SUCCESS_MESSAGE = f"Вы выполнили все условия! Получите материал: {REWARD_LINK}"
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"channels": CHANNELS, "stats": STATS,
                       "users": list(USERS), "reward_link": REWARD_LINK},
                      f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")

load_data()

# ──────────────────────────────────────────────
#  Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
    encoding="utf-8"
)
def _exc_hook(t, v, tb):
    if issubclass(t, UnicodeEncodeError): return
    logging.error("Необработанная ошибка", exc_info=(t, v, tb))
sys.excepthook = _exc_hook
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  Bot & FastAPI init
# ──────────────────────────────────────────────
bot    = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
router = Router()
app    = FastAPI()
BOT_ID = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # разрешаем запросы с GitHub Pages
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════════
#  API endpoint — вызывается из Mini Web App
# ══════════════════════════════════════════════
@app.get("/check")
async def api_check_subscription(user_id: int):
    """
    Проверяет подписки пользователя и возвращает JSON:
    {
      "subscribed": true/false,
      "reward_link": "...",          # только если subscribed=true
      "channels": [
        {"name": "...", "channel_id": "...", "type": "public/private",
         "invite_link": "...", "subscribed": true/false}
      ]
    }
    """
    channels_status = []
    all_subscribed = True

    for channel_name, channel_id, channel_type, invite_link in CHANNELS:
        is_sub = await _check_channel(user_id, channel_id, channel_type)
        if not is_sub:
            all_subscribed = False
        channels_status.append({
            "name": channel_name,
            "channel_id": channel_id,
            "type": channel_type,
            "invite_link": invite_link,
            "subscribed": is_sub
        })

    response = {"subscribed": all_subscribed, "channels": channels_status}
    if all_subscribed:
        response["reward_link"] = REWARD_LINK
        STATS["users_subscribed"] += 1
        USERS.add(user_id)
        save_data()

    return JSONResponse(content=response)

# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────
async def _check_channel(user_id, channel_id, channel_type):
    try:
        chat_id = f"@{channel_id}" if channel_type == "public" else channel_id
        m = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return m.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки канала {channel_id}: {e}")
        return False

async def _bot_is_admin(channel_id, channel_type):
    try:
        chat_id = f"@{channel_id}" if channel_type == "public" else channel_id
        m = await bot.get_chat_member(chat_id=chat_id, user_id=BOT_ID)
        return m.status in ['administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки прав бота в {channel_id}: {e}")
        return False

def generate_instruction_message():
    msg = "Чтобы получить материалы, подпишитесь на каналы:\n\n"
    for i, (name, cid, ctype, link) in enumerate(CHANNELS):
        href = link if ctype == "private" else f"https://t.me/{cid}"
        msg += f"{i+1}. <a href='{href}'>{name}</a>\n"
    msg += "\nПосле подписки нажмите кнопку ниже 👇"
    return msg

# ──────────────────────────────────────────────
#  Admin panel UI helpers
# ──────────────────────────────────────────────
def generate_admin_panel_message():
    txt = f"""📊 <b>Статистика бота:</b>
Подписались на все каналы: {STATS["users_subscribed"]} раз
Всего пользователей: {len(USERS)}"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Каналы", callback_data='admin_channels_0')],
        [InlineKeyboardButton(text="🔗 Ссылка", callback_data='admin_link'),
         InlineKeyboardButton(text="✏️ Изменить", callback_data='admin_edit_link')],
        [InlineKeyboardButton(text="📊 Обновить статистику", callback_data='admin_update_stats')],
        [InlineKeyboardButton(text="📩 Рассылка", callback_data='admin_broadcast')],
        [InlineKeyboardButton(text="📢 Опубликовать пост в каналы", callback_data='admin_post_to_channels')],
    ])
    return txt, kb

def generate_channels_message(page=0):
    msg = "📋 <b>Список каналов:</b>\n"
    kb  = InlineKeyboardMarkup(inline_keyboard=[])
    if not CHANNELS:
        msg += "Список пуст.\n\nВыберите действие:"
    else:
        PER_PAGE = 5
        total = len(CHANNELS); pages = (total + PER_PAGE - 1) // PER_PAGE
        page  = max(0, min(page, pages - 1))
        chunk = CHANNELS[page*PER_PAGE:(page+1)*PER_PAGE]
        for i, (name, cid, ctype, link) in enumerate(chunk, start=page*PER_PAGE+1):
            emoji = "🔒" if ctype == "private" else "🌐"
            msg += f"{i}. {emoji} {name} (ID: {cid})\n"
        msg += "\nВыберите действие:"
        for name, cid, ctype, link in chunk:
            href = link if ctype == "private" else f"https://t.me/{cid}"
            kb.inline_keyboard.append([
                InlineKeyboardButton(text=name, url=href),
                InlineKeyboardButton(text="Удалить ❌", callback_data=f"delete_channel|{cid}")
            ])
        if total > PER_PAGE:
            kb.inline_keyboard.append([
                InlineKeyboardButton(text="⬅️", callback_data=f"admin_channels_{(page-1)%pages}"),
                InlineKeyboardButton(text="➡️", callback_data=f"admin_channels_{(page+1)%pages}")
            ])
    kb.inline_keyboard += [
        [InlineKeyboardButton(text="➕ Добавить канал", callback_data='admin_add_channel')],
        [InlineKeyboardButton(text="🗑️ Очистить все", callback_data='admin_clear_channels_confirm')],
        [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data='admin_back')],
    ]
    return msg, kb

def generate_post_channel_selection(selected: list):
    rows = []
    for name, cid, *_ in CHANNELS:
        mark = "✅" if cid in selected else "⬜"
        rows.append([InlineKeyboardButton(text=f"{mark} {name}", callback_data=f"post_toggle|{cid}")])
    rows.append([
        InlineKeyboardButton(text="✅ Выбрать все", callback_data="post_select_all"),
        InlineKeyboardButton(text="❌ Снять все",   callback_data="post_deselect_all"),
    ])
    if selected:
        rows.append([InlineKeyboardButton(text=f"📤 Опубликовать ({len(selected)})", callback_data="post_confirm_send")])
    rows.append([InlineKeyboardButton(text="🚫 Отмена", callback_data="post_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════
@router.message(Command(commands=["start"]))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    USERS.add(uid); STATS["users_started"] += 1; save_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 СТАРТ", callback_data='start')
    ]])
    await message.answer("Нажмите кнопку 🚀 СТАРТ, чтобы получить список каналов.", reply_markup=kb)

@router.callback_query(lambda c: c.data == 'start')
async def cb_start(call: types.CallbackQuery):
    uid = call.from_user.id; USERS.add(uid); save_data()
    if not CHANNELS:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Перезапустить", callback_data='restart')],
            [InlineKeyboardButton(text="📥 Попасть в подборку", callback_data='selection')]
        ])
        await call.message.answer("Каналы ещё не добавлены, ожидайте 🌙", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Получить материалы", callback_data='check')],
            [InlineKeyboardButton(text="📥 Попасть в подборку", callback_data='selection')]
        ])
        await call.message.answer(generate_instruction_message(), reply_markup=kb,
                                  disable_web_page_preview=True,
                                  message_effect_id="5104841245755180586")
    await call.answer()

# ══════════════════════════════════════════════
#  Кнопка для поста в канале
#  Чтобы опубликовать пост с кнопкой-Mini App вручную:
#
#  kb = InlineKeyboardMarkup(inline_keyboard=[[
#      InlineKeyboardButton(text="🎁 Получить подарок",
#                           web_app=WebAppInfo(url=MINI_APP_URL))
#  ]])
#  await bot.send_message(chat_id="@your_channel", text="Ваш пост", reply_markup=kb)
#
#  Через админ-панель — используйте кнопку "📢 Опубликовать пост"
#  (бот автоматически добавит кнопку Mini App к посту при публикации)
# ══════════════════════════════════════════════

@router.callback_query(lambda c: c.data == 'restart')
async def cb_restart(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 СТАРТ", callback_data='start')]])
    await call.message.answer("Нажмите 🚀 СТАРТ.", reply_markup=kb)
    await call.answer()

@router.callback_query(lambda c: c.data == 'check')
async def cb_check(call: types.CallbackQuery):
    uid = call.from_user.id
    all_ok = True; missing = []
    for ch in CHANNELS:
        if not await _check_channel(uid, ch[1], ch[2]):
            all_ok = False; missing.append(ch)
    if all_ok:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Перезапустить", callback_data='restart')
        ]])
        await call.message.answer(SUCCESS_MESSAGE, reply_markup=kb,
                                  disable_web_page_preview=True,
                                  message_effect_id="5046509860389126442")
        STATS["users_subscribed"] += 1; save_data()
    else:
        missing_text = "\n".join(
            [f"{i+1}. <a href='{il if ct=='private' else f'https://t.me/{cid}'}'>{cn}</a> ❌"
             for i,(cn,cid,ct,il) in enumerate(missing)])
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Проверить снова", callback_data='check')
        ]])
        await call.message.answer("Не подписаны:\n" + missing_text, reply_markup=kb,
                                  disable_web_page_preview=True)
    await call.answer()

@router.callback_query(lambda c: c.data == 'selection')
async def cb_selection(call: types.CallbackQuery):
    await call.message.answer("Подробности: @Ekaterina_Swm")
    await call.answer()

# ══════════════════════════════════════════════
#  /admin
# ══════════════════════════════════════════════
@router.message(Command(commands=["admin"]))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Нет доступа."); return
    txt, kb = generate_admin_panel_message()
    await message.answer(txt, reply_markup=kb)

# ══════════════════════════════════════════════
#  Admin callbacks
# ══════════════════════════════════════════════
@router.callback_query(lambda c: (
    c.data.startswith('admin_') or c.data.startswith('delete_channel|')
    or c.data.startswith('post_')
    or c.data in ['cancel_broadcast','cancel_edit_link',
                  'confirm_clear_channels','cancel_clear_channels']
))
async def handle_admin_callbacks(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа."); return

    global CHANNELS
    action = call.data

    if action.startswith('admin_channels_'):
        page = int(action.split('_')[-1])
        msg, kb = generate_channels_message(page)
        await call.message.edit_text(msg, reply_markup=kb, disable_web_page_preview=True)

    elif action == 'admin_add_channel':
        await call.message.answer(
            "📢 Введите ссылку на канал (https://t.me/channel или https://t.me/+invite):")
        await state.set_state(AdminStates.waiting_for_channel_link)
        await call.message.delete()

    elif action == 'admin_clear_channels_confirm':
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Да, удалить", callback_data='confirm_clear_channels'),
            InlineKeyboardButton(text="Отмена",       callback_data='cancel_clear_channels')
        ]])
        await call.message.answer("🗑️ Удалить все каналы?", reply_markup=kb)

    elif action == 'confirm_clear_channels':
        CHANNELS.clear(); save_data()
        await call.message.answer("🗑️ Все каналы удалены.")
        msg, kb = generate_channels_message(0)
        await call.message.answer(msg, reply_markup=kb, disable_web_page_preview=True)

    elif action == 'cancel_clear_channels':
        await call.message.answer("❌ Отменено.")
        msg, kb = generate_channels_message(0)
        await call.message.answer(msg, reply_markup=kb, disable_web_page_preview=True)

    elif action.startswith('delete_channel|'):
        cid = action.split('|')[-1]
        before = len(CHANNELS)
        CHANNELS[:] = [ch for ch in CHANNELS if ch[1] != cid]
        if len(CHANNELS) < before: save_data(); await call.message.answer("🗑️ Канал удалён.")
        else: await call.message.answer("❌ Канал не найден.")
        msg, kb = generate_channels_message(0)
        await call.message.answer(msg, reply_markup=kb, disable_web_page_preview=True)

    elif action == 'admin_link':
        await call.message.answer(f"🔗 Текущая ссылка:\n{REWARD_LINK}")

    elif action == 'admin_edit_link':
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data='cancel_edit_link')]])
        await call.message.answer(f"✏️ Текущая ссылка: {REWARD_LINK}\nВведите новую:", reply_markup=kb)
        await state.set_state(AdminStates.waiting_for_reward_link)

    elif action == 'cancel_edit_link':
        await call.message.answer("❌ Отменено.")
        txt, kb = generate_admin_panel_message()
        await call.message.answer(txt, reply_markup=kb)

    elif action == 'admin_update_stats':
        await call.message.delete()
        txt, kb = generate_admin_panel_message()
        await call.message.answer(txt, reply_markup=kb)

    elif action == 'admin_broadcast':
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data='cancel_broadcast')]])
        await call.message.answer("📩 Отправьте сообщение для рассылки:", reply_markup=kb)
        await state.set_state(AdminStates.waiting_for_broadcast_message)

    elif action == 'cancel_broadcast':
        await call.message.answer("❌ Рассылка отменена.")
        txt, kb = generate_admin_panel_message()
        await call.message.answer(txt, reply_markup=kb)

    elif action == 'admin_back':
        txt, kb = generate_admin_panel_message()
        await call.message.edit_text(txt, reply_markup=kb)

    # ── Post to channels ──
    elif action == 'admin_post_to_channels':
        if not CHANNELS:
            await call.message.answer("❌ Нет каналов. Добавьте каналы сначала.")
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data='post_cancel')]])
            await call.message.answer(
                "📢 <b>Публикация поста</b>\n\n"
                "Отправьте сообщение. Бот автоматически добавит к нему кнопку «🎁 Получить подарок» с Mini App.",
                reply_markup=kb)
            await state.set_state(AdminStates.waiting_for_post_message)

    elif action.startswith('post_toggle|'):
        cid = action.split('|')[-1]
        data = await state.get_data()
        selected = data.get("post_selected_channels", [])
        if cid in selected: selected.remove(cid)
        else: selected.append(cid)
        await state.update_data(post_selected_channels=selected)
        await call.message.edit_reply_markup(reply_markup=generate_post_channel_selection(selected))

    elif action == 'post_select_all':
        selected = [ch[1] for ch in CHANNELS]
        await state.update_data(post_selected_channels=selected)
        await call.message.edit_reply_markup(reply_markup=generate_post_channel_selection(selected))

    elif action == 'post_deselect_all':
        await state.update_data(post_selected_channels=[])
        await call.message.edit_reply_markup(reply_markup=generate_post_channel_selection([]))

    elif action == 'post_confirm_send':
        data = await state.get_data()
        selected  = data.get("post_selected_channels", [])
        from_chat = data.get("post_from_chat")
        msg_id    = data.get("post_message_id")
        if not selected:
            await call.answer("⚠️ Выберите хотя бы один канал!", show_alert=True); return
        if not msg_id:
            await call.answer("⚠️ Пост не найден. Начните заново.", show_alert=True)
            await state.clear(); return

        await call.message.answer(f"📤 Публикую в {len(selected)} канал(ах)...")
        ok, fail = 0, []
        # Кнопка Mini App добавляется автоматически к каждому посту
        post_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎁 Получить подарок", web_app=WebAppInfo(url=MINI_APP_URL))
        ]])
        for cid in selected:
            ch = next((c for c in CHANNELS if c[1] == cid), None)
            if not ch: continue
            target = f"@{ch[1]}" if ch[2] == "public" else ch[1]
            try:
                await bot.copy_message(
                    chat_id=target,
                    from_chat_id=from_chat,
                    message_id=msg_id,
                    reply_markup=post_kb   # ← кнопка Mini App
                )
                ok += 1
            except Exception as e:
                fail.append(ch[0]); logger.error(f"Ошибка публикации в {ch[1]}: {e}")

        report = f"✅ Опубликовано в {ok} канал(ах)."
        if fail: report += f"\n❌ Ошибка: {', '.join(fail)}"
        await call.message.answer(report)
        await state.clear()
        txt, kb = generate_admin_panel_message()
        await call.message.answer(txt, reply_markup=kb)

    elif action == 'post_cancel':
        await call.message.answer("❌ Публикация отменена.")
        await state.clear()
        txt, kb = generate_admin_panel_message()
        await call.message.answer(txt, reply_markup=kb)

    await call.answer()

# ══════════════════════════════════════════════
#  FSM handlers
# ══════════════════════════════════════════════
@router.message(AdminStates.waiting_for_channel_link)
async def fsm_channel_link(message: types.Message, state: FSMContext):
    try:
        txt = message.text.strip()
        if not txt.startswith("https://t.me/"):
            raise ValueError("Введите ссылку вида https://t.me/...")
        part = txt.split("/")[3]
        if part.startswith("+"):
            await state.update_data(invite_link=txt)
            await state.set_state(AdminStates.waiting_for_private_channel_id)
            await message.answer("🔒 Введите ID приватного канала (например, -1001234567890):")
        else:
            if any(ch[1] == part for ch in CHANNELS): raise ValueError("Канал уже добавлен!")
            if not await _bot_is_admin(part, "public"): raise ValueError("Бот не админ в этом канале!")
            chat = await bot.get_chat(f"@{part}")
            CHANNELS.append((chat.title, part, "public", None)); save_data()
            await message.answer(f"✅ Канал <a href='https://t.me/{part}'>{chat.title}</a> добавлен!")
            msg, kb = generate_channels_message(0)
            await message.answer(msg, reply_markup=kb, disable_web_page_preview=True)
            await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}"); await state.clear()

@router.message(AdminStates.waiting_for_private_channel_id)
async def fsm_private_channel_id(message: types.Message, state: FSMContext):
    data = await state.get_data(); invite_link = data.get("invite_link")
    try:
        cid = message.text.strip()
        if not cid.startswith("-"): raise ValueError("ID должен начинаться с '-'")
        if any(ch[1] == cid for ch in CHANNELS): raise ValueError("Канал уже добавлен!")
        if not await _bot_is_admin(cid, "private"): raise ValueError("Бот не админ в этом канале!")
        chat = await bot.get_chat(cid)
        CHANNELS.append((chat.title, cid, "private", invite_link)); save_data()
        await message.answer(f"✅ Канал <a href='{invite_link}'>{chat.title}</a> добавлен!")
        msg, kb = generate_channels_message(0)
        await message.answer(msg, reply_markup=kb, disable_web_page_preview=True)
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}"); await state.clear()

@router.message(AdminStates.waiting_for_reward_link)
async def fsm_reward_link(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    global REWARD_LINK, SUCCESS_MESSAGE
    REWARD_LINK = message.text.strip()
    SUCCESS_MESSAGE = f"Вы выполнили все условия! Получите материал: {REWARD_LINK}"
    save_data()
    await message.answer(f"🎁 Ссылка обновлена:\n{REWARD_LINK}")
    txt, kb = generate_admin_panel_message()
    await message.answer(txt, reply_markup=kb)
    await state.clear()

@router.message(AdminStates.waiting_for_broadcast_message)
async def fsm_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    ok = fail = 0
    await message.answer(f"📩 Рассылка для {len(USERS)} пользователей...")
    for uid in USERS:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
            ok += 1
        except: fail += 1
    await message.answer(f"📩 Готово. Успешно: {ok}, ошибок: {fail}")
    txt, kb = generate_admin_panel_message()
    await message.answer(txt, reply_markup=kb)
    await state.clear()

@router.message(AdminStates.waiting_for_post_message)
async def fsm_post_message(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.update_data(post_from_chat=message.chat.id, post_message_id=message.message_id,
                            post_selected_channels=[])
    await message.answer("📋 <b>Выберите каналы для публикации:</b>",
                         reply_markup=generate_post_channel_selection([]))
    await state.set_state(AdminStates.waiting_for_post_channel_selection)

# ══════════════════════════════════════════════
#  Main — запускает бота + API одновременно
# ══════════════════════════════════════════════
async def run_bot():
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

async def main():
    global BOT_ID
    me = await bot.get_me()
    BOT_ID = me.id
    logger.info(f"Бот запущен. ID: {BOT_ID}")
    logger.info(f"API доступен на http://0.0.0.0:8000/check")

    # Запускаем бота и FastAPI параллельно
    bot_task = asyncio.create_task(run_bot())
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    api_task = asyncio.create_task(server.serve())
    await asyncio.gather(bot_task, api_task)

if __name__ == "__main__":
    logger.info("Запуск...")
    asyncio.run(main())
