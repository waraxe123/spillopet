import asyncio
import speedtest
import html
import re
import os
import io

import subprocess
import textwrap
import traceback
from contextlib import redirect_stdout
from statistics import mean
from time import monotonic as time

from telethon import events

from telegram.error import TelegramError
from telegram.error import ChatMigrated
from telegram.error import BadRequest
from telegram.error import Unauthorized
from telegram import Update
from telegram import ParseMode
from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.ext import Filters

import OdaRobot
from OdaRobot.__main__ import STATS
from OdaRobot import telethn
from OdaRobot import OWNER_ID
from OdaRobot import DEV_USERS
from OdaRobot import log
from OdaRobot.modules.helper_funcs.chat_status import dev_plus
from OdaRobot.modules.helper_funcs.chat_status import sudo_plus
from OdaRobot.modules.helper_funcs.decorators import odacmd
from OdaRobot.modules.helper_funcs.decorators import odacallback


@odacmd(command="lockdown")
@dev_plus
def allow_groups(update: Update, context: CallbackContext):
    args = context.args
    if not args:
        update.effective_message.reply_text(
            f"Current state: {OdaRobot.ALLOW_CHATS}", allow_sending_without_reply=True
        )
        return
    if args[0].lower() in ["off", "no"]:
        OdaRobot.ALLOW_CHATS = True
    elif args[0].lower() in ["yes", "on"]:
        OdaRobot.ALLOW_CHATS = False
    else:
        update.effective_message.reply_text(
            "Format: /lockdown Yes/No or Off/On", allow_sending_without_reply=True
        )
        return
    update.effective_message.reply_text(
        "Done! Lockdown value toggled.", allow_sending_without_reply=True
    )


@odacmd(command="leave")
@dev_plus
def leave(update: Update, context: CallbackContext):
    bot = context.bot
    args = context.args
    if args:
        chat_id = str(args[0])
        leave_msg = " ".join(args[1:])
        try:
            context.bot.send_message(chat_id, leave_msg)
            bot.leave_chat(int(chat_id))
            update.effective_message.reply_text(
                "Left chat.", allow_sending_without_reply=True
            )
        except (TelegramError, BadRequest, ChatMigrated, Unauthorized):
            update.effective_message.reply_text(
                "Failed to leave chat for some reason.",
                allow_sending_without_reply=True,
            )
    else:
        chat = update.effective_chat
        kb = [
            [
                InlineKeyboardButton(
                    text="I am sure of this action.",
                    callback_data="leavechat_cb_({})".format(chat.id),
                )
            ]
        ]
        update.effective_message.reply_text(
            "I'm going to leave {}, press the button below to confirm".format(
                chat.title
            ),
            reply_markup=InlineKeyboardMarkup(kb),
            allow_sending_without_reply=True,
        )


@odacallback(pattern=r"leavechat_cb_.*")
def leave_cb(update: Update, context: CallbackContext):
    bot = context.bot
    callback = update.callback_query
    if callback.from_user.id not in OWNER_ID:
        callback.answer(text="This isn't for you", show_alert=True)
        return

    match = re.match(r"leavechat_cb_\((.+?)\)", callback.data)
    chat = int(match.group(1))
    bot.leave_chat(chat_id=chat)
    callback.answer(text="Left chat")


class Store:
    def __init__(self, func):
        self.func = func
        self.calls = []
        self.time = time()
        self.lock = asyncio.Lock()

    def average(self):
        return round(mean(self.calls), 2) if self.calls else 0

    def __repr__(self):
        return f"<Store func={self.func.__name__}, average={self.average()}>"

    async def __call__(self, event):
        async with self.lock:
            if not self.calls:
                self.calls = [0]
            if time() - self.time > 1:
                self.time = time()
                self.calls.append(1)
            else:
                self.calls[-1] += 1
        await self.func(event)


async def nothing(event):
    pass


messages = Store(nothing)
inline_queries = Store(nothing)
callback_queries = Store(nothing)

telethn.add_event_handler(messages, events.NewMessage())
telethn.add_event_handler(inline_queries, events.InlineQuery())
telethn.add_event_handler(callback_queries, events.CallbackQuery())


@telethn.on(events.NewMessage(pattern=r"/getstats", from_users=OWNER_ID))
async def getstats(event):
    await event.reply(
        f"**__ODA EVENT STATISTICS__**\n**Average messages:** {messages.average()}/s\n**Average Callback Queries:** {callback_queries.average()}/s\n**Average Inline Queries:** {inline_queries.average()}/s",
        parse_mode="md",
    )


@odacmd(command="getchat")
@dev_plus
def get_chat_by_id(update: Update, context: CallbackContext):
    msg = update.effective_message
    args = context.args
    if not args:
        msg.reply_text(
            "<i>Chat ID required</i>",
            parse_mode=ParseMode.HTML,
            allow_sending_without_reply=True,
        )
        return
    if len(args) >= 1:
        data = context.bot.get_chat(args[0])
        m = "<b>Found chat, below are the details.</b>\n\n"
        m += "<b>Title</b>: {}\n".format(html.escape(data.title))
        m += "<b>Members</b>: {}\n\n".format(data.get_member_count())
        if data.description:
            m += "<i>{}</i>\n\n".format(html.escape(data.description))
        if data.linked_chat_id:
            m += "<b>Linked chat</b>: {}\n".format(data.linked_chat_id)

        m += "<b>Type</b>: {}\n".format(data.type)
        if data.username:
            m += "<b>Username</b>: {}\n".format(html.escape(data.username))
        m += "<b>ID</b>: {}\n".format(data.id)
        m += "\n<b>Permissions</b>:\n <code>{}</code>\n".format(data.permissions)

        if data.invite_link:
            m += "\n<b>Invitelink</b>: {}".format(data.invite_link)

        msg.reply_text(
            text=m, parse_mode=ParseMode.HTML, allow_sending_without_reply=True
        )


@odacmd(command=["sh", "shell"])
@dev_plus
def shell(update: Update, context: CallbackContext):
    message = update.effective_message
    cmd = message.text.split(" ", 1)
    if len(cmd) == 1:
        message.reply_text(
            "No command to execute was given.", allow_sending_without_reply=True
        )
        return
    cmd = cmd[1]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
    )
    stdout, stderr = process.communicate()
    reply = ""
    stderr = stderr.decode()
    stdout = stdout.decode()
    if stdout:
        reply += f"*Stdout*\n`{stdout}`\n"
        log.info(f"Shell - {cmd} - {stdout}")
    if stderr:
        reply += f"*Stderr*\n`{stderr}`\n"
        log.error(f"Shell - {cmd} - {stderr}")
    if len(reply) > 3000:
        with open("shell_output.txt", "w") as file:
            file.write(reply)
        with open("shell_output.txt", "rb") as doc:
            context.bot.send_document(
                document=doc,
                filename=doc.name,
                reply_to_message_id=message.message_id,
                chat_id=message.chat_id,
            )
    else:
        message.reply_text(
            reply, parse_mode=ParseMode.MARKDOWN, allow_sending_without_reply=True
        )


def convert(speed):
    return round(int(speed) / 1048576, 2)


@odacmd(command="speedtest")
@dev_plus
def speedtestxyz(update: Update, context: CallbackContext):
    buttons = [
        [
            InlineKeyboardButton("Image", callback_data="speedtest_image"),
            InlineKeyboardButton("Text", callback_data="speedtest_text"),
        ]
    ]
    update.effective_message.reply_text(
        "Select SpeedTest Mode",
        reply_markup=InlineKeyboardMarkup(buttons),
        allow_sending_without_reply=True,
    )


@odacallback(pattern=r"speedtest_.*")
def speedtestxyz_callback(update: Update, _):
    query = update.callback_query

    if query.from_user.id in DEV_USERS:
        msg = update.effective_message.edit_text("Running a speedtest....")
        speed = speedtest.Speedtest()
        speed.get_best_server()
        speed.download()
        speed.upload()
        replymsg = "SpeedTest Results:"

        if query.data == "speedtest_image":
            speedtest_image = speed.results.share()
            update.effective_message.reply_photo(
                photo=speedtest_image, caption=replymsg
            )
            msg.delete()

        elif query.data == "speedtest_text":
            result = speed.results.dict()
            replymsg += f"\nDownload: `{convert(result['download'])}Mb/s`\nUpload: `{convert(result['upload'])}Mb/s`\nPing: `{result['ping']}`"
            update.effective_message.edit_text(replymsg, parse_mode=ParseMode.MARKDOWN)
    else:
        query.answer("You are required to join {SUPPORT_CHAT} to use this command.")


namespaces = {}


def namespace_of(chat, update, bot):
    if chat not in namespaces:
        namespaces[chat] = {
            "__builtins__": globals()["__builtins__"],
            "bot": bot,
            "effective_message": update.effective_message,
            "effective_user": update.effective_user,
            "effective_chat": update.effective_chat,
            "update": update,
        }

    return namespaces[chat]


def log_input(update):
    user = update.effective_user.id
    chat = update.effective_chat.id
    log.info(f"IN: {update.effective_message.text} (user={user}, chat={chat})")


def send(msg, bot, update):
    if len(str(msg)) > 2000:
        with io.BytesIO(str.encode(msg)) as out_file:
            out_file.name = "output.txt"
            bot.send_document(chat_id=update.effective_chat.id, document=out_file)
    else:
        log.info(f"OUT: '{msg}'")
        bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"`{msg}`",
            parse_mode=ParseMode.MARKDOWN,
        )


@odacmd(command=["e", "ev", "eva", "eval"], filters=Filters.user(DEV_USERS))
@dev_plus
def evaluate(update: Update, context: CallbackContext):
    bot = context.bot
    send(do(eval, bot, update), bot, update)


@odacmd(command=["x", "ex", "exe", "py"], filters=Filters.user(DEV_USERS))
@dev_plus
def execute(update: Update, context: CallbackContext):
    bot = context.bot
    send(do(exec, bot, update), bot, update)


def cleanup_code(code):
    if code.startswith("```") and code.endswith("```"):
        return "\n".join(code.split("\n")[1:-1])
    return code.strip("` \n")


def do(func, bot, update):
    log_input(update)
    content = update.message.text.split(" ", 1)[-1]
    body = cleanup_code(content)
    env = namespace_of(update.message.chat_id, update, bot)

    os.chdir(os.getcwd())
    with open(
        os.path.join(os.getcwd(), "OdaRobot/modules/helper_funcs/temp.txt"),
        "w",
    ) as temp:
        temp.write(body)

    stdout = io.StringIO()

    to_compile = f'def func():\n{textwrap.indent(body, "  ")}'

    try:
        exec(to_compile, env)
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"

    func = env["func"]

    try:
        with redirect_stdout(stdout):
            func_return = func()
    except Exception as e:
        value = stdout.getvalue()
        return f"{value}{traceback.format_exc()}"
    else:
        value = stdout.getvalue()
        result = None
        if func_return is None:
            if value:
                result = f"{value}"
            else:
                try:
                    result = f"{repr(eval(body, env))}"
                except ZeroDivisionError:
                    pass
        else:
            result = f"{value}{func_return}"
        if result:
            return result


@odacmd(command="clearlocals")
@dev_plus
def clear(update: Update, context: CallbackContext):
    bot = context.bot
    log_input(update)
    if update.message.chat_id in namespaces:
        del namespaces[update.message.chat_id]
    send("Cleared locals.", bot, update)


@odacmd(command="stats")
@sudo_plus
def stats(update: Update, context: CallbackContext):
    stats = "<b>╒═══「 System statistics 」</b>\n" + "\n".join(
        [mod.__stats__() for mod in STATS]
    )
    result = re.sub(r"(\d+)", r"<code>\1</code>", stats)
    update.effective_message.reply_text(
        result, parse_mode=ParseMode.HTML, allow_sending_without_reply=True
    )


@odacmd(command="snipe")
@dev_plus
def snipe(update: Update, context: CallbackContext):
    args = context.args
    bot = context.bot
    try:
        chat_id = str(args[0])
        del args[0]
    except TypeError:
        update.effective_message.reply_text(
            "Please give me a chat to echo to!", allow_sending_without_reply=True
        )
    to_send = " ".join(args)
    if len(to_send) >= 2:
        try:
            bot.sendMessage(int(chat_id), str(to_send))
        except TelegramError:
            log.warning("Couldn't send to group %s", str(chat_id))
            update.effective_message.reply_text(
                "Couldn't send the message. Perhaps I'm not part of that group?",
                allow_sending_without_reply=True,
            )
