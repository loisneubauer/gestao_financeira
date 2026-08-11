# emailer.py - Envio de emails transacionais (convite de novo usuário) via Gmail SMTP
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _config_disponivel():
    return bool(os.environ.get("GMAIL_USER")) and bool(os.environ.get("GMAIL_APP_PASSWORD"))


def enviar_email_convite(destinatario, nome_usuario, nome_organizacao, email_login, senha_temporaria, url_login):
    """Envia o email de boas-vindas com as credenciais de acesso e o aviso de
    troca de senha obrigatória. Retorna (sucesso: bool, mensagem: str)."""
    if not _config_disponivel():
        return False, "Envio de email não configurado (faltam GMAIL_USER / GMAIL_APP_PASSWORD)."

    remetente = os.environ["GMAIL_USER"]
    senha_app = os.environ["GMAIL_APP_PASSWORD"]

    assunto = f"Bem-vindo(a) ao Gestão Financeira — {nome_organizacao}"

    corpo_texto = f"""Olá, {nome_usuario}!

Sua conta no Gestão Financeira foi criada para a organização "{nome_organizacao}".

Acesse: {url_login}
Email de login: {email_login}
Senha temporária: {senha_temporaria}

Por segurança, você vai precisar trocar essa senha assim que fizer o primeiro login.

Qualquer dúvida, é só responder este email.
"""

    corpo_html = f"""
    <div style="font-family: Arial, sans-serif; color: #333; max-width: 480px;">
        <h2 style="color: #6B4E8E;">Bem-vindo(a) ao Gestão Financeira</h2>
        <p>Olá, <strong>{nome_usuario}</strong>!</p>
        <p>Sua conta foi criada para a organização <strong>{nome_organizacao}</strong>.</p>
        <table style="margin: 16px 0; border-collapse: collapse;">
            <tr><td style="padding: 4px 8px; color: #666;">Acesse</td><td style="padding: 4px 8px;"><a href="{url_login}">{url_login}</a></td></tr>
            <tr><td style="padding: 4px 8px; color: #666;">Email de login</td><td style="padding: 4px 8px;"><strong>{email_login}</strong></td></tr>
            <tr><td style="padding: 4px 8px; color: #666;">Senha temporária</td><td style="padding: 4px 8px;"><code>{senha_temporaria}</code></td></tr>
        </table>
        <p style="background: #FFF3CD; padding: 10px 14px; border-radius: 6px;">
            ⚠️ Por segurança, você vai precisar <strong>trocar essa senha</strong> assim que fizer o primeiro login.
        </p>
        <p style="color: #888; font-size: 13px;">Qualquer dúvida, é só responder este email.</p>
    </div>
    """

    mensagem = MIMEMultipart("alternative")
    mensagem["Subject"] = assunto
    mensagem["From"] = remetente
    mensagem["To"] = destinatario
    mensagem.attach(MIMEText(corpo_texto, "plain"))
    mensagem.attach(MIMEText(corpo_html, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as servidor:
            servidor.starttls()
            servidor.login(remetente, senha_app)
            servidor.sendmail(remetente, destinatario, mensagem.as_string())
        return True, "Email de convite enviado com sucesso."
    except Exception as e:
        return False, f"Falha ao enviar email: {e}"
