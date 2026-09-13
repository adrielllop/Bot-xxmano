# Bot de vendas Telegram

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../bot.env.example .env
nano .env
python bot.py
```

No `.env`, substitua `BOT_TOKEN` pelo novo token criado no @BotFather. O administrador já está fixado em `8744681561`.

## MystiqPay

Preencha `MYSTIQ_PUBLIC_ID` e `MYSTIQ_PRIVATE_ID` com o Client ID e Client Secret. Os caminhos da API são fixos no código e não precisam ser cadastrados no AppMint. A MisticPay também exige `MYSTIQ_DEFAULT_PAYER_DOCUMENT` (CPF do pagador sem pontuação) para criar o PIX; sem esse campo, a cobrança não é criada. Nunca use CPF fictício. O código não considera comprovante enviado pelo usuário como pagamento aprovado. A entrega automática após confirmação exige implementar o endpoint de consulta/webhook e o formato exato de resposta/status da MisticPay.

## Segurança

Não publique o `.env`, o banco `bot.sqlite3` nem tokens. O token anteriormente enviado deve ser revogado no @BotFather.
