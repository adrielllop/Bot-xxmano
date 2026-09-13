import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "8744681561"))
DB_PATH = os.getenv("DB_PATH", "bot.sqlite3")
# URL oficial fixa da API; não precisa ser informada no arquivo .env.
MYSTIQ_BASE = "https://api.misticpay.com/api"
# Endpoints oficiais fixos; não dependem de variáveis extras no AppMint.
MYSTIQ_CREATE_PATH = "/transactions/create"
MYSTIQ_STATUS_PATH = "/transactions/check"
MYSTIQ_PUBLIC = os.getenv("MYSTIQ_PUBLIC_ID", "").strip()
MYSTIQ_PRIVATE = os.getenv("MYSTIQ_PRIVATE_ID", "").strip()

# Conversation states
P_NAME, P_PRICE, P_DESC, P_PHOTO, P_STOCK_MODE, P_STOCK = range(6)
QTY = 10
STOCK_ADD = 11


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, price_cents INTEGER NOT NULL,
            description TEXT NOT NULL, photo_file_id TEXT,
            stock_mode TEXT NOT NULL DEFAULT 'finite', active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
            content TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY, product_id INTEGER NOT NULL, buyer_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL, total_cents INTEGER NOT NULL, status TEXT NOT NULL,
            payment_id TEXT, created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """)


def money(cents: int) -> str:
    return f"R$ {cents / 100:.2f}".replace(".", ",")


def admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id == ADMIN_ID)


def products_keyboard():
    with db() as con:
        rows = con.execute("SELECT id, name, price_cents FROM products WHERE active=1 ORDER BY id DESC").fetchall()
    buttons = [[InlineKeyboardButton(f"{r['name']} — {money(r['price_cents'])}", callback_data=f"buy:{r['id']}")] for r in rows]
    buttons.append([InlineKeyboardButton("Voltar", callback_data="home")])
    return InlineKeyboardMarkup(buttons)


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Criar produto", callback_data="adm:create")],
        [InlineKeyboardButton("Gerenciar produtos", callback_data="adm:manage")],
        [InlineKeyboardButton("Voltar", callback_data="home")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Bem-vindo! Escolha uma opção:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Ver produtos", callback_data="products")],
        [InlineKeyboardButton("Painel ADM", callback_data="admin")],
    ]))


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "home":
        await q.edit_message_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Ver produtos", callback_data="products")],
            [InlineKeyboardButton("Painel ADM", callback_data="admin")],
        ]))
    elif data == "products":
        if not products_keyboard().inline_keyboard[0:-1]:
            await q.edit_message_text("Nenhum produto disponível no momento.")
        else:
            await q.edit_message_text("Produtos disponíveis:", reply_markup=products_keyboard())
    elif data == "admin":
        if not admin(update):
            await q.edit_message_text("Acesso negado.")
        else:
            await q.edit_message_text("Painel ADM", reply_markup=admin_keyboard())
    elif data.startswith("buy:"):
        pid = int(data.split(":")[1])
        with db() as con:
            p = con.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p:
            await q.edit_message_text("Produto não encontrado.")
            return
        context.user_data["buy_product"] = pid
        text = f"*{p['name']}*\n{p['description']}\nPreço unitário: {money(p['price_cents'])}\n\nDigite a quantidade:"
        if p["photo_file_id"]:
            await q.message.reply_photo(photo=p["photo_file_id"], caption=text, parse_mode=ParseMode.MARKDOWN)
            await q.edit_message_text("A imagem do produto foi enviada acima. Digite a quantidade:")
        else:
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        context.user_data["state"] = QTY
    elif data == "adm:create":
        if not admin(update): return await q.edit_message_text("Acesso negado.")
        context.user_data.clear(); context.user_data["state"] = P_NAME
        await q.edit_message_text("Digite o nome do produto:")
    elif data == "adm:manage":
        if not admin(update): return await q.edit_message_text("Acesso negado.")
        with db() as con:
            rows = con.execute("SELECT p.*, (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.delivered=0) qtd FROM products p WHERE p.active=1 ORDER BY p.id DESC").fetchall()
        if not rows:
            await q.edit_message_text("Nenhum produto cadastrado.", reply_markup=admin_keyboard()); return
        buttons = [[InlineKeyboardButton(f"{r['name']} | estoque: {'∞' if r['stock_mode']=='infinite' else r['qtd']}", callback_data=f"manage:{r['id']}")] for r in rows]
        buttons.append([InlineKeyboardButton("Voltar", callback_data="admin")])
        await q.edit_message_text("Gerenciar produtos:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("manage:"):
        if not admin(update): return await q.edit_message_text("Acesso negado.")
        pid = int(data.split(":")[1])
        with db() as con:
            p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            qtd = con.execute("SELECT COUNT(*) FROM stock WHERE product_id=? AND delivered=0", (pid,)).fetchone()[0]
        await q.edit_message_text(f"*{p['name']}*\nPreço: {money(p['price_cents'])}\nEstoque: {'infinito' if p['stock_mode']=='infinite' else qtd}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Adicionar estoque", callback_data=f"stockadd:{pid}"), InlineKeyboardButton("Remover estoque", callback_data=f"stockdel:{pid}")],
            [InlineKeyboardButton("Desativar produto", callback_data=f"disable:{pid}")],
            [InlineKeyboardButton("Voltar", callback_data="adm:manage")],
        ]))
    elif data.startswith("stockadd:"):
        if not admin(update): return await q.edit_message_text("Acesso negado.")
        context.user_data["stock_product"] = int(data.split(":")[1]); context.user_data["state"] = STOCK_ADD
        await q.edit_message_text("Envie os itens do estoque, um por linha. Para itens finitos, cada item deve ficar separado por ¢.\nExemplo: chave-1¢chave-2¢chave-3")
    elif data.startswith("stockdel:"):
        if not admin(update): return await q.edit_message_text("Acesso negado.")
        pid = int(data.split(":")[1])
        with db() as con:
            con.execute("DELETE FROM stock WHERE product_id=? AND delivered=0", (pid,))
        await q.edit_message_text("Estoque disponível removido.", reply_markup=admin_keyboard())
    elif data.startswith("disable:"):
        if not admin(update): return await q.edit_message_text("Acesso negado.")
        with db() as con: con.execute("UPDATE products SET active=0 WHERE id=?", (int(data.split(":")[1]),))
        await q.edit_message_text("Produto desativado.", reply_markup=admin_keyboard())
async def text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = (update.message.text or "").strip()
    if state == QTY:
        try: qty = int(text); assert qty > 0
        except Exception: return await update.message.reply_text("Digite uma quantidade inteira maior que zero.")
        pid = context.user_data["buy_product"]
        with db() as con: p = con.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p: return await update.message.reply_text("Produto indisponível.")
        if p['stock_mode'] == 'finite':
            with db() as con: available = con.execute("SELECT COUNT(*) FROM stock WHERE product_id=? AND delivered=0", (pid,)).fetchone()[0]
            if qty > available: return await update.message.reply_text(f"Estoque insuficiente. Disponível: {available}.")
        order = str(uuid.uuid4())
        with db() as con: con.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?)", (order, pid, update.effective_user.id, qty, p['price_cents'] * qty, datetime.now(timezone.utc).isoformat()))
        payment = await create_payment(order, p['name'], p['price_cents'] * qty, update.effective_user.full_name)
        if not payment: return await update.message.reply_text("O pagamento ainda não está configurado. O administrador precisa vincular a MystiqPay.")
        context.user_data.clear()
        await update.message.reply_text(f"Pedido criado: {order}\nValor: {money(p['price_cents'] * qty)}\n\nPague usando o link/QR abaixo. Após a confirmação automática, o produto será enviado neste chat:\n{payment}")
    elif state == P_NAME:
        context.user_data['name'] = text; context.user_data['state'] = P_PRICE; await update.message.reply_text("Digite o preço em reais, por exemplo: 19,90")
    elif state == P_PRICE:
        try: cents = int(round(float(text.replace(',', '.')) * 100)); assert cents > 0
        except Exception: return await update.message.reply_text("Preço inválido. Exemplo: 19,90")
        context.user_data['price'] = cents; context.user_data['state'] = P_DESC; await update.message.reply_text("Digite a descrição:")
    elif state == P_DESC:
        context.user_data['desc'] = text; context.user_data['state'] = P_PHOTO; await update.message.reply_text("Envie a foto do produto, ou digite 'sem foto':")
    elif state == P_PHOTO and text.lower() == 'sem foto':
        context.user_data['photo'] = None; context.user_data['state'] = P_STOCK_MODE; await update.message.reply_text("O estoque será finito ou infinito?")
    elif state == P_STOCK_MODE:
        mode = text.lower()
        if mode not in ('finito', 'infinito'): return await update.message.reply_text("Responda finito ou infinito.")
        context.user_data['mode'] = 'finite' if mode == 'finito' else 'infinite'; context.user_data['state'] = P_STOCK if mode == 'finito' else None
        if mode == 'finito': await update.message.reply_text("Envie os itens do estoque, separados por ¢. Exemplo: item1¢item2¢item3")
        else: await finish_product(update, context, '')
    elif state == P_STOCK:
        await finish_product(update, context, text)
    elif state == STOCK_ADD:
        pid = context.user_data.get("stock_product")
        with db() as con:
            p = con.execute("SELECT stock_mode FROM products WHERE id=?", (pid,)).fetchone()
            if not p:
                return await update.message.reply_text("Produto não encontrado.")
            if p["stock_mode"] == "infinite":
                return await update.message.reply_text("Este produto tem estoque infinito; não é necessário adicionar itens.")
            items = [x.strip() for x in text.split("¢") if x.strip()]
            con.executemany("INSERT INTO stock(product_id,content) VALUES(?,?)", [(pid, x) for x in items])
        context.user_data.clear()
        await update.message.reply_text(f"{len(items)} item(ns) adicionado(s) ao estoque.", reply_markup=admin_keyboard())
    elif state == P_PHOTO:
        await update.message.reply_text("Envie uma foto válida ou digite 'sem foto'.")


async def photo_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != P_PHOTO or not admin(update): return
    context.user_data['photo'] = update.message.photo[-1].file_id; context.user_data['state'] = P_STOCK_MODE
    await update.message.reply_text("O estoque será finito ou infinito?")


async def finish_product(update, context, stock_text):
    d = context.user_data
    with db() as con:
        cur = con.execute("INSERT INTO products(name,price_cents,description,photo_file_id,stock_mode,created_at) VALUES(?,?,?,?,?,?)", (d['name'], d['price'], d['desc'], d.get('photo'), d['mode'], datetime.now(timezone.utc).isoformat()))
        pid = cur.lastrowid
        if d['mode'] == 'finite':
            items = [x.strip() for x in stock_text.split('¢') if x.strip()]
            con.executemany("INSERT INTO stock(product_id,content) VALUES(?,?)", [(pid, x) for x in items])
    context.user_data.clear(); await update.message.reply_text("Produto criado com sucesso.", reply_markup=admin_keyboard())


async def create_payment(order_id: str, description: str, amount_cents: int, payer_name: str) -> Optional[str]:
    if not all((MYSTIQ_BASE, MYSTIQ_PUBLIC, MYSTIQ_PRIVATE)): return None
    # A MisticPay exige autenticação HTTP Basic e estes campos no cash-in PIX.
    import base64
    basic = base64.b64encode(f"{MYSTIQ_PUBLIC}:{MYSTIQ_PRIVATE}".encode()).decode()
    payload = {
        'amount': amount_cents / 100,
        'payerName': payer_name[:100] or 'Cliente Telegram',
        # A API exige CPF. O bot ainda não solicita CPF; use o campo abaixo
        # somente se o fluxo de coleta de CPF for acrescentado.
        'payerDocument': os.getenv('MYSTIQ_DEFAULT_PAYER_DOCUMENT', '').strip(),
        'transactionId': order_id,
        'description': description[:200],
    }
    if not payload['payerDocument']:
        log.error('MYSTIQ_DEFAULT_PAYER_DOCUMENT não configurado; a MisticPay exige payerDocument.')
        return None
    headers = {'Authorization': f'Basic {basic}', 'Content-Type': 'application/json'}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(MYSTIQ_BASE + MYSTIQ_CREATE_PATH, json=payload, headers=headers)
        r.raise_for_status(); data = r.json()
    result = data.get('data', data)
    return result.get('qrcodeUrl') or result.get('copyPaste') or result.get('payment_url')


async def error_handler(update, context): log.exception("Erro no bot", exc_info=context.error)


def main():
    if not BOT_TOKEN or BOT_TOKEN == 'COLE_O_NOVO_TOKEN_AQUI': raise SystemExit('Configure BOT_TOKEN no arquivo .env')
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.PHOTO, photo_flow))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))
    app.add_error_handler(error_handler)
    log.info('Bot iniciado. Administrador: %s', ADMIN_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__': main()
