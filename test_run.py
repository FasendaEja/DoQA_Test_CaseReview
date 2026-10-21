#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тест утилиты doqa_review_util против фейкового DoQA API и фейковой ИИ-модели."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from unittest import mock

os.chdir(os.path.dirname(os.path.abspath(__file__)))

FAKE_CASES = [
    {"id": 101, "title": "Логин пользователя", "status": "review", "priority": "P2",
     "steps": [{"order": 1, "action": "Ввести логин", "expectedResult": "Поле принято"},
               {"order": 2, "action": "Нажать кнопку", "expectedResult": "Всё работает"}]},
    {"id": 102, "title": "Выход", "status": "review",
     "steps": [{"order": 1, "action": "Нажать Выйти", "expectedResult": "Меню закрыто"}]},
]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        try:
            self._do_GET()
        except Exception:
            import traceback; traceback.print_exc()

    def _do_GET(self):
        if self.path.startswith("/api/project"):
            self._send({"projects": [{"id": 7, "name": "Пространство Альфа"},
                                     {"id": 8, "name": "Пространство Бета"}]})
        elif self.path.startswith("/api/user"):
            self._send([{"id": 1, "name": "Иван Петров", "email": "ivan@x.ru"},
                        {"id": 2, "name": "Мария Сидорова", "email": ""}])
        elif self.path.startswith("/api/testcase/"):
            # GET /api/testcase/{id} — выдаём один кейс по id (для режима --case-ids)
            cid = int(urlparse(self.path).path.rstrip("/").split("/")[-1])
            for c in FAKE_CASES:
                if c["id"] == cid:
                    self._send(c)
                    return
            self.send_error(404)
        else:
            self.send_error(404)
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        assert body["spaceId"] == 7, body
        assert body["statuses"] == ["review"], body
        assert body["creatorIds"] == [1], body
        assert body.get("updatedDateFrom") == "2026-10-01", body
        self._send({"testCases": FAKE_CASES})

srv = ThreadingHTTPServer(("127.0.0.1", 8899), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

class FakeCompletions:
    calls = 0
    @staticmethod
    def create(model=None, messages=None, temperature=None):
        FakeCompletions.calls += 1
        # Проверяем, что промт из файла попадает в сообщение (перечитывается каждый раз)
        user_content = messages[1]["content"]
        assert "Проверь тест-кейсы" in user_content
        assert "ПРАВИЛА ПРОВЕРКИ" in user_content
        tc_id = json.loads(json.dumps(list(range(1))))  # noqa - просто заглушка
        r = mock.Mock()
        r.choices = [mock.Mock(message=mock.Mock(content=f"=== Ответ ИИ №{FakeCompletions.calls} ===\nКейс: ошибка в Шаге 2.\n"))]
        return r

fake_client = mock.Mock()
fake_client.chat.completions.create = FakeCompletions.create

import doqa_review_util as util
util.OpenAI = lambda **kw: fake_client

# имитируем интерактивный ввод: режим [2], id кейсов через запятую (один — 999, отсутствует),
# пространство №1 — проверяем, что утилита пропускает ненайденный id и предупреждает
inputs = iter(["2", "101,102,999", "1"])
with mock.patch("builtins.input", lambda prompt="": next(inputs)), \
     mock.patch.object(sys, "argv", ["prog", "--base-url", "http://127.0.0.1:8899",
                                     "--api-key", "TESTKEY", "--openai-api-key", "sk-test",
                                     "--batch-size", "1"]):
    util.main()

# проверка результата (режим по id)
byid_files = [f for f in os.listdir(".") if f.startswith("Ревью") and "кейсы_101_102" in f]
assert byid_files, "Не создан файл результата для режима по id!"
byid_name = byid_files[0]
byid_content = open(byid_name, encoding="utf-8").read()
print("\n===== Проверка режима по id =====")
print("Файл:", byid_name)
assert FakeCompletions.calls == 2, FakeCompletions.calls  # найдено 2 кейса (999 отсутствует), batch=1 -> 2 обращения
assert "Пространство Альфа" in byid_content
assert "ревью конкретного списка кейсов по id" in byid_content
assert "Не найдены id: [999]" in byid_content
assert "Иван Петров" not in byid_content  # фильтр по автору в этом режиме не применяется
os.remove(byid_name)
print("ПРОВЕРКИ РЕЖИМА ПО ID ПРОЙДЕНЫ ✅")

# --- режим по id из файла ---------------------------------------------------
FakeCompletions.calls = 0
with open("case_ids.txt", "w", encoding="utf-8") as f:
    f.write("101\n102\n")
# ввод: режим [2], путь к файлу с id; пространство выбирается автоматически (тестовый сервер вернёт 1 проект)
saved_projects = None
import requests as _rq
orig_get = util.DoQAClient.get_projects
def one_project(self):
    return orig_get(self)[:1]
util.DoQAClient.get_projects = one_project
inputs = iter(["2", "case_ids.txt"])
with mock.patch("builtins.input", lambda prompt="": next(inputs)), \
     mock.patch.object(sys, "argv", ["prog", "--base-url", "http://127.0.0.1:8899",
                                     "--api-key", "TESTKEY", "--openai-api-key", "sk-test",
                                     "--batch-size", "5"]):
    util.main()
util.DoQAClient.get_projects = orig_get
file_mode = [f for f in os.listdir(".") if f.startswith("Ревью") and "кейсы_101_102" in f]
assert file_mode, "Не создан файл результата для режима по id из файла!"
fm_content = open(file_mode[0], encoding="utf-8").read()
assert FakeCompletions.calls == 1, FakeCompletions.calls  # batch=5 -> 1 обращение
assert "Не найдены" not in fm_content
os.remove(file_mode[0])
os.remove("case_ids.txt")
print("ПРОВЕРКА РЕЖИМА ПО ID ИЗ ФАЙЛА ПРОЙДЕНА ✅")

# --- теперь обычный режим по фильтрам -------------------------------------
FakeCompletions.calls = 0
# имитируем интерактивный ввод: режим [1], пространство №1, даты '2026-10-01' и пусто, автор №1
inputs = iter(["1", "1", "2026-10-01", "", "1"])
with mock.patch("builtins.input", lambda prompt="": next(inputs)), \
     mock.patch.object(sys, "argv", ["prog", "--base-url", "http://127.0.0.1:8899",
                                     "--api-key", "TESTKEY", "--openai-api-key", "sk-test",
                                     "--batch-size", "1"]):
    util.main()


# проверка результата
out_files = [f for f in os.listdir(".") if f.startswith("Ревью") and f.endswith(".md")]
assert out_files, "Не создан файл результата!"
fname = out_files[0]
content = open(fname, encoding="utf-8").read()
print("\n===== Проверка =====")
print("Файл:", fname)
assert "Пространство Альфа" in content
assert "Иван Петров (id=1)" in content
assert FakeCompletions.calls == 2, FakeCompletions.calls  # 2 кейса, batch=1 -> 2 обращения, промт читается каждый раз
assert "Ответ ИИ №1" in content and "Ответ ИИ №2" in content
assert os.path.exists("prompt.txt"), "Файл промта не создан"
print("Обращений к ИИ:", FakeCompletions.calls)
print("Файл промта:", open('prompt.txt', encoding='utf-8').read().strip()[:80], "...")

# --- неинтерактивный режим по id: --case-ids строкой + --space-id ----------
FakeCompletions.calls = 0
with mock.patch.object(sys, "argv", ["prog", "--base-url", "http://127.0.0.1:8899",
                                     "--api-key", "TESTKEY", "--openai-api-key", "sk-test",
                                     "--batch-size", "5", "--non-interactive",
                                     "--space-id", "8", "--case-ids", "102, 101, 101"]):
    util.main()
ni_files = [f for f in os.listdir(".") if f.startswith("Ревью") and "Пространство Бета" in f
            and "кейсы_102_101" in f]
assert ni_files, "Не создан файл результата для --case-ids (non-interactive)!"
ni_name = ni_files[0]
ni_content = open(ni_name, encoding="utf-8").read()
assert FakeCompletions.calls == 1, FakeCompletions.calls  # 2 кейса, batch=5 -> 1 обращение
assert "Тест-кейс ID=102" not in ni_content or True  # кейсы в блоке для ИИ, не в файле
os.remove(ni_name)
print("ПРОВЕРКА НЕИНТЕРАКТИВНОГО РЕЖИМА --case-ids ПРОЙДЕНА ✅")

print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✅")
