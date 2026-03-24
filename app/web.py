from flask import Flask, Response, abort, jsonify, redirect, render_template_string, request

from app.config import settings
from app.notifier_bitrix import Bitrix24WebhookConnector
from app.storage import ProcessedMessageStorage
from app.imap_client import (
    connect_mail,
    extract_message_meta,
    fetch_full_message_by_uid,
    verify_message_view_link,
)

# from config import settings
# from notifier_bitrix import Bitrix24WebhookConnector
# from storage import ProcessedMessageStorage
# from imap_client import (
#     connect_mail,
#     extract_message_meta,
#     fetch_full_message_by_uid,
#     verify_message_view_link,
# )

app = Flask(__name__)

storage = ProcessedMessageStorage(settings.sqlite_db)
storage.init_db()
bx = Bitrix24WebhookConnector()

HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Mail Observer Setup</title>
</head>
<body>
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
    .toplink {
      display: inline-block;
      margin-top: 12px;
    }
  </style>
</head>
<body>
  <div class="card">
    <h1>{{ meta.subject or "(без темы)" }}</h1>

    <div class="row"><span class="label">От:</span> {{ meta["from"] or "—" }}</div>
    <div class="row"><span class="label">Кому:</span> {{ meta["to"] or "—" }}</div>
    <div class="row"><span class="label">Дата:</span> {{ meta["date"] or "—" }}</div>
    <div class="row"><span class="label">UID:</span> {{ meta["uid"] or "—" }}</div>
    <div class="row"><span class="label">Message-ID:</span> {{ meta["message_id"] or "—" }}</div>

    <div class="text">{{ meta["text"] or "(не удалось извлечь текст письма)" }}</div>
  </div>
</body>
</html>
"""


def valid_token_or_404(token: str):
    real = storage.get_or_create_setup_token()
    if token != real:
        return False
    return True


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/mail/<uid>")
def view_mail(uid: str):
    mailbox = (request.args.get("mb") or "INBOX").strip() or "INBOX"
    expires = (request.args.get("e") or "").strip()
    signature = (request.args.get("s") or "").strip()

    secret = storage.get_or_create_setup_token()
    if not verify_message_view_link(uid, mailbox, expires, signature, secret):
        return abort(403)

    cfg = storage.get_runtime_config()
    yandex_email = (cfg.get("yandex_email") or "").strip()
    yandex_app_password = (cfg.get("yandex_app_password") or "").strip()

    if not yandex_email or not yandex_app_password:
        return Response("Yandex credentials are not configured", status=500)

    mail = None
    try:
        mail = connect_mail(
            host="imap.yandex.com",
            port=993,
            user_email=yandex_email,
            app_password=yandex_app_password,
            mailbox=mailbox,
            readonly=True,
        )

        message = fetch_full_message_by_uid(mail, uid)
        if not message:
            return abort(404)

        meta = extract_message_meta(message, uid=uid)
        return render_template_string(MAIL_VIEW_HTML, meta=meta)

    except Exception as e:
        return Response(f"failed to open message: {e}", status=500)
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


@app.get("/")
def root():
    token = storage.get_or_create_setup_token()
    return redirect(f"/setup/{token}")


@app.get("/setup/<token>")
def setup_page(token: str):
    if not valid_token_or_404(token):
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
def save_settings(token: str):
    if not valid_token_or_404(token):
        return "invalid token", 404

    yandex_email = (request.form.get("yandex_email") or "").strip()
    yandex_app_password = (request.form.get("yandex_app_password") or "").strip()

    storage.save_runtime_config(yandex_email, yandex_app_password)
    return redirect(f"/setup/{token}?saved=1")


@app.get("/setup/<token>/api/search-users")
def api_search_users(token: str):
    if not valid_token_or_404(token):
        return jsonify({"ok": False, "error": "invalid token"}), 404

    q = (request.args.get("q") or "").strip()
    items = bx.search_users_by_fio(q, limit=10)
    return jsonify({"ok": True, "items": items})


@app.post("/setup/<token>/api/recipients/add")
def api_add_recipient(token: str):
    if not valid_token_or_404(token):
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
def api_remove_recipient(token: str):
    if not valid_token_or_404(token):
        return jsonify({"ok": False, "error": "invalid token"}), 404

    data = request.get_json(silent=True) or {}
    bitrix_user_id = str(data.get("bitrix_user_id") or "").strip()

    if not bitrix_user_id:
        return jsonify({"ok": False, "error": "bitrix_user_id is required"}), 400

    storage.delete_recipient(bitrix_user_id)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host=settings.web_host, port=settings.web_port, debug=False)