from flask import Flask, jsonify, redirect, render_template_string, request

from app.config import settings
from app.notifier_bitrix import Bitrix24WebhookConnector
from app.storage import ProcessedMessageStorage

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


def valid_token_or_404(token: str):
    real = storage.get_or_create_setup_token()
    if token != real:
        return False
    return True


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


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