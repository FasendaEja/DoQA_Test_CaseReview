#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тест утилиты doqa_review_util против фейкового DoQA API и фейковой ИИ-модели."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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
        if self.path.startswith("/api/project"):
            self._send({"projects": [{"id": 7, "name": "Пространство Альфа"},
                                     {"id": 8, "name": "Пространство Бета"}]})
        elif self.path.startswith("/api/user"):
            self._send([{"id": 1, "name": "Иван Петров", "email": "ivan@x.ru"},
                        {"id": 2, "name": "Мария Сидорова", "email": ""}])
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

srv = HTTPServer(("127.0.0.1", 8899), Handler)
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

# имитируем интерактивный ввод: пространство №1, даты '2026-10-01' и пусто, автор №1
inputs = iter(["1", "2026-10-01", "", "1"])
with mock.patch("builtins.input", lambda prompt="": next(inputs)), \
     mock.patch.object(sys, "argv", ["prog", "--base-url", "http://127.0.0.1:8899",
                                     "--api-key", "TESTKEY", "--openai-api-key", "sk-test",
                                     "--batch-size", "1"]):
    util.main()

srv.shutdown()

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
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✅")
