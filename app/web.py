import hashlib
import hmac
import secrets
import time
from functools import wraps
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, render_template_string, request, abort, make_response

from app.config import settings
from app.formatter import format_date_ru
from app.imap_client import (
    connect_mail,
    extract_message_meta,
    fetch_full_message_by_uid,
    verify_message_view_link,
)
from app.notifier_bitrix import Bitrix24WebhookConnector
from app.storage import ProcessedMessageStorage

app = Flask(__name__)

storage = ProcessedMessageStorage(settings.sqlite_db)
storage.init_db()
bx = Bitrix24WebhookConnector()


AUTH_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Вход в Mail Observer</title>
</head>
<body>
  <h2>Вход в админку Mail Observer</h2>

  {% if error %}
    <div style="color:red;"><strong>{{ error }}</strong></div>
    <br>
  {% endif %}

  <form method="post" action="/auth/send-code">
    <input type="hidden" name="next" value="{{ next_url }}">
    <div>
      <label>Bitrix ID администратора</label><br>
      <input type="text" name="bitrix_user_id" value="{{ bitrix_user_id }}" style="width: 320px;">
    </div>
    <br>
    <button type="submit">Получить код в Bitrix</button>
  </form>

  <hr>

  <form method="post" action="/auth/verify">
    <input type="hidden" name="next" value="{{ next_url }}">
    <div>
      <label>Bitrix ID администратора</label><br>
      <input type="text" name="bitrix_user_id" value="{{ bitrix_user_id }}" style="width: 320px;">
    </div>
    <br>
    <div>
      <label>Код из Bitrix</label><br>
      <input type="text" name="code" value="" style="width: 320px;">
    </div>
    <br>
    <button type="submit">Войти</button>
  </form>
</body>
</html>
"""


HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Mail Observer Setup</title>
</head>
<body>
  <div style="float:right;">
    <a href="/logout">Выйти</a>
  </div>

  <h2>Настройка Mail Observer</h2>

  <form method="post" action="/setup/{{ token }}/save-settings">
    <div>
      <label>YANDEX_EMAIL</label><br>
      <input type="text" name="yandex_email" value="{{ yandex_email }}" style="width: 420px;">
    </div>
    <br>
    <div>
      <label>YANDEX_APP_PASSWORD</label><br>
      <input type="password" name="yandex_app_password" value="{{ yandex_app_password }}" style="width: 420px;">
    </div>
    <br>
    <button type="submit">Сохранить настройки</button>
  </form>

  <hr>

  <h3>Получатели</h3>

  <div>
    <label>Поиск сотрудника по ФИО</label><br>
    <input type="text" id="fioQuery" style="width: 420px;" placeholder="Например: Иванов Иван Иванович">
    <button type="button" onclick="searchUsers()">Найти</button>
  </div>

  <p>Количество сотрудников, которым сейчас идут уведомления: <strong>{{ recipients|length }}</strong></p>

  <div id="searchResults"></div>

  <hr>

  <h3>Текущие получатели</h3>
  <div id="recipientsList">
    {% for r in recipients %}
      <div>
        {{ r["fio"] }} {% if r["email"] %}({{ r["email"] }}){% endif %}
        — BX24 ID: {{ r["bitrix_user_id"] }}
        <button type="button" onclick="removeRecipient('{{ r['bitrix_user_id'] }}')">Удалить</button>
      </div>
    {% else %}
      <div>Получателей пока нет.</div>
    {% endfor %}
  </div>

  {% if saved %}
    <hr>
    <div><strong>Сохранено.</strong></div>
  {% endif %}

<script>
async function searchUsers() {
  const q = document.getElementById("fioQuery").value.trim();
  if (!q) {
    alert("Введите ФИО");
    return;
  }

  const res = await fetch(`/setup/{{ token }}/api/search-users?q=` + encodeURIComponent(q));
  const data = await res.json();

  const box = document.getElementById("searchResults");
  box.innerHTML = "";

  if (!data.items || !data.items.length) {
    box.innerHTML = "<div>Ничего не найдено</div>";
    return;
  }

  data.items.forEach(item => {
    const div = document.createElement("div");
    div.innerHTML = `
      <div>
        <strong>${item.fio}</strong>
        ${item.email ? "(" + item.email + ")" : ""}
        ${item.position ? " — " + item.position : ""}
        — BX24 ID: ${item.id}
        <button type="button" onclick="addRecipient('${item.id}', '${escapeQuotes(item.fio)}', '${escapeQuotes(item.email || "")}')">Добавить</button>
      </div>
    `;
    box.appendChild(div);
  });
}

function escapeQuotes(s) {
  return String(s).replace(/'/g, "\\'");
}

async function addRecipient(id, fio, email) {
  const res = await fetch(`/setup/{{ token }}/api/recipients/add`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      bitrix_user_id: id,
      fio: fio,
      email: email
    })
  });

  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Ошибка");
    return;
  }
  location.reload();
}

async function removeRecipient(id) {
  const res = await fetch(`/setup/{{ token }}/api/recipients/remove`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      bitrix_user_id: id
    })
  });

  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Ошибка");
    return;
  }
  location.reload();
}
</script>
</body>
</html>
"""


MAIL_VIEW_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{{ meta.subject or "(без темы)" }}</title>
  <style>
    body {
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: #f6f7f9;
      color: #111;
      margin: 0;
      padding: 24px;
    }
    .card {
      max-width: 980px;
      margin: 0 auto;
      background: #fff;
      border: 1px solid #ddd;
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 1px 3px rgba(0,0,0,.06);
    }
    h1 {
      margin-top: 0;
      font-size: 24px;
      line-height: 1.3;
    }
    .row {
      margin: 8px 0;
    }
    .label {
      display: inline-block;
      min-width: 92px;
      color: #666;
      vertical-align: top;
    }
    .text {
      margin-top: 20px;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.5;
      background: #fafafa;
      border: 1px solid #eee;
      border-radius: 12px;
      padding: 16px;
    }
  </style>
</head>
<body>
  <div class="card">
    <h1>{{ meta.subject or "(без темы)" }}</h1>

    <div class="row"><span class="label">От:</span> {{ meta["from"] or "—" }}</div>
    <div class="row"><span class="label">Кому:</span> {{ meta["to"] or "—" }}</div>
    <div class="row"><span class="label">Дата:</span> {{ meta["date"] or "—" }}</div>
    <div class="row"><span class="label">Открыть почту: <a href="https://mail.yandex.ru" >https://mail.yandex.ru</a></span></div>

    <div class="text">{{ meta["text"] or "(не удалось извлечь текст письма)" }}</div>
  </div>
</body>
</html>
"""


def allowed_admin_ids() -> set[str]:
    out = set()
    if getattr(settings, "bitrix_admin_id_1", ""):
        out.add(str(settings.bitrix_admin_id_1).strip())
    if getattr(settings, "bitrix_admin_id_2", ""):
        out.add(str(settings.bitrix_admin_id_2).strip())
    return out


def hash_login_code(bitrix_user_id: str, code: str) -> str:
    raw = f"{bitrix_user_id}|{code}|{settings.web_session_secret}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def make_next_url(next_url: str | None) -> str:
    token = storage.get_or_create_setup_token()
    default_url = f"/setup/{token}"

    if not next_url:
        return default_url

    next_url = str(next_url).strip()
    if not next_url.startswith("/"):
        return default_url

    return next_url


def valid_token(token: str) -> bool:
    real = storage.get_or_create_setup_token()
    return token == real


def get_admin_session():
    token = request.cookies.get("admin_session")
    if not token:
        return None

    row = storage.get_admin_session(token)
    if not row:
        return None

    now_ts = int(time.time())
    if int(row["expires_at"]) < now_ts:
        storage.delete_admin_session(token)
        return None

    new_expires = now_ts + settings.admin_session_ttl_seconds
    storage.touch_admin_session(token, new_expires)
    return row


def require_admin_session(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        session_row = get_admin_session()
        if not session_row:
            next_url = request.path
            if request.query_string:
                next_url += "?" + request.query_string.decode()
            q = urlencode({"next": next_url, "admin": settings.bitrix_admin_id_1 or ""})
            return redirect(f"/auth?{q}")
        return view_func(*args, **kwargs)
    return wrapper


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/")
def root():
    token = storage.get_or_create_setup_token()
    q = urlencode({"next": f"/setup/{token}", "admin": settings.bitrix_admin_id_1 or ""})
    return redirect(f"/auth?{q}")


@app.get("/auth")
def auth_page():
    return render_template_string(
        AUTH_HTML,
        next_url=make_next_url(request.args.get("next")),
        bitrix_user_id=(request.args.get("admin") or "").strip(),
        error=(request.args.get("error") or "").strip(),
    )


@app.post("/auth/send-code")
def auth_send_code():
    if not settings.web_session_secret:
        return "WEB_SESSION_SECRET не задан", 500

    bitrix_user_id = (request.form.get("bitrix_user_id") or "").strip()
    next_url = make_next_url(request.form.get("next"))

    if bitrix_user_id not in allowed_admin_ids():
        q = urlencode({
            "next": next_url,
            "admin": bitrix_user_id,
            "error": "Этот Bitrix ID не имеет доступа",
        })
        return redirect(f"/auth?{q}")

    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = hash_login_code(bitrix_user_id, code)
    expires_at = int(time.time()) + settings.admin_login_code_ttl_seconds

    storage.create_admin_login_code(bitrix_user_id, code_hash, expires_at)

    text = (
        f"Код входа в Mail Observer: {code}\n"
        f"Код действует {settings.admin_login_code_ttl_seconds // 60} мин."
    )

    ok, dbg = bx.send_msg_user(bitrix_user_id, text)
    if not ok:
        q = urlencode({
            "next": next_url,
            "admin": bitrix_user_id,
            "error": f"Не удалось отправить код в Bitrix: {dbg}",
        })
        return redirect(f"/auth?{q}")

    q = urlencode({
        "next": next_url,
        "admin": bitrix_user_id,
        "error": "Код отправлен в Bitrix. Введите его ниже.",
    })
    return redirect(f"/auth?{q}")


@app.post("/auth/verify")
def auth_verify():
    if not settings.web_session_secret:
        return "WEB_SESSION_SECRET не задан", 500

    bitrix_user_id = (request.form.get("bitrix_user_id") or "").strip()
    code = (request.form.get("code") or "").strip()
    next_url = make_next_url(request.form.get("next"))

    if bitrix_user_id not in allowed_admin_ids():
        q = urlencode({
            "next": next_url,
            "admin": bitrix_user_id,
            "error": "Этот Bitrix ID не имеет доступа",
        })
        return redirect(f"/auth?{q}")

    row = storage.get_latest_admin_login_code(bitrix_user_id)
    now_ts = int(time.time())

    if not row:
        q = urlencode({
            "next": next_url,
            "admin": bitrix_user_id,
            "error": "Сначала запросите код",
        })
        return redirect(f"/auth?{q}")

    if row["used_at"] is not None:
        q = urlencode({
            "next": next_url,
            "admin": bitrix_user_id,
            "error": "Код уже использован. Запросите новый.",
        })
        return redirect(f"/auth?{q}")

    if int(row["expires_at"]) < now_ts:
        q = urlencode({
            "next": next_url,
            "admin": bitrix_user_id,
            "error": "Код истёк. Запросите новый.",
        })
        return redirect(f"/auth?{q}")

    if int(row["attempts"]) >= settings.admin_max_code_attempts:
        q = urlencode({
            "next": next_url,
            "admin": bitrix_user_id,
            "error": "Превышено число попыток. Запросите новый код.",
        })
        return redirect(f"/auth?{q}")

    expected_hash = hash_login_code(bitrix_user_id, code)
    if not hmac.compare_digest(expected_hash, row["code_hash"]):
        storage.increment_admin_login_code_attempts(int(row["id"]))
        q = urlencode({
            "next": next_url,
            "admin": bitrix_user_id,
            "error": "Неверный код",
        })
        return redirect(f"/auth?{q}")

    storage.mark_admin_login_code_used(int(row["id"]))

    session_token = secrets.token_urlsafe(32)
    expires_at = now_ts + settings.admin_session_ttl_seconds
    storage.create_admin_session(bitrix_user_id, session_token, expires_at)

    response = make_response(redirect(next_url))
    response.set_cookie(
        "admin_session",
        session_token,
        max_age=settings.admin_session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="Lax",
    )
    return response


@app.get("/logout")
def logout():
    token = request.cookies.get("admin_session")
    if token:
        storage.delete_admin_session(token)

    response = make_response(redirect("/auth"))
    response.delete_cookie("admin_session")
    return response


@app.get("/setup/<token>")
@require_admin_session
def setup_page(token: str):
    if not valid_token(token):
        return "invalid token", 404

    cfg = storage.get_runtime_config()
    recipients = storage.list_recipients(active_only=False)

    return render_template_string(
        HTML,
        token=token,
        yandex_email=cfg["yandex_email"],
        yandex_app_password=cfg["yandex_app_password"],
        recipients=recipients,
        saved=request.args.get("saved") == "1",
    )


@app.post("/setup/<token>/save-settings")
@require_admin_session
def save_settings(token: str):
    if not valid_token(token):
        return "invalid token", 404

    yandex_email = (request.form.get("yandex_email") or "").strip()
    yandex_app_password = (request.form.get("yandex_app_password") or "").strip()

    storage.save_runtime_config(yandex_email, yandex_app_password)
    return redirect(f"/setup/{token}?saved=1")


@app.get("/setup/<token>/api/search-users")
@require_admin_session
def api_search_users(token: str):
    if not valid_token(token):
        return jsonify({"ok": False, "error": "invalid token"}), 404

    q = (request.args.get("q") or "").strip()
    items = bx.search_users_by_fio(q, limit=10)
    return jsonify({"ok": True, "items": items})


@app.post("/setup/<token>/api/recipients/add")
@require_admin_session
def api_add_recipient(token: str):
    if not valid_token(token):
        return jsonify({"ok": False, "error": "invalid token"}), 404

    data = request.get_json(silent=True) or {}
    bitrix_user_id = str(data.get("bitrix_user_id") or "").strip()
    fio = str(data.get("fio") or "").strip()
    email = str(data.get("email") or "").strip()

    if not bitrix_user_id:
        return jsonify({"ok": False, "error": "bitrix_user_id is required"}), 400

    storage.upsert_recipient(bitrix_user_id, fio, email)
    return jsonify({"ok": True})


@app.post("/setup/<token>/api/recipients/remove")
@require_admin_session
def api_remove_recipient(token: str):
    if not valid_token(token):
        return jsonify({"ok": False, "error": "invalid token"}), 404

    data = request.get_json(silent=True) or {}
    bitrix_user_id = str(data.get("bitrix_user_id") or "").strip()

    if not bitrix_user_id:
        return jsonify({"ok": False, "error": "bitrix_user_id is required"}), 400

    storage.delete_recipient(bitrix_user_id)
    return jsonify({"ok": True})


@app.get("/mail/<uid>")
def view_mail(uid: str):
    mailbox = (request.args.get("mb") or "INBOX").strip()
    expires = (request.args.get("e") or "").strip()
    signature = (request.args.get("s") or "").strip()

    if not settings.mail_link_secret:
        return "MAIL_LINK_SECRET не задан", 500

    if not verify_message_view_link(uid, mailbox, expires, signature, settings.mail_link_secret):
        abort(403)

    cfg = storage.get_runtime_config()
    yandex_email = cfg["yandex_email"]
    yandex_app_password = cfg["yandex_app_password"]

    if not yandex_email or not yandex_app_password:
        return "Yandex settings are not configured", 500

    mail = connect_mail(
        host=settings.imap_host,
        port=settings.imap_port,
        user_email=yandex_email,
        app_password=yandex_app_password,
        mailbox=mailbox,
        readonly=True,
    )

    try:
        message = fetch_full_message_by_uid(mail, uid)
        if not message:
            abort(404)

        meta = extract_message_meta(message, uid=uid)
        meta["date"] = format_date_ru(meta.get("date", ""))
        return render_template_string(MAIL_VIEW_HTML, meta=meta)
    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    app.run(host=settings.web_host, port=settings.web_port, debug=False)

