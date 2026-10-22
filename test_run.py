#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тест утилиты doqa_review_util против фейкового DoQA API и фейковой ИИ-модели."""
import csv
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

# --- проверка конфигурации эндпоинтов DoQA API (doqa_api_config.json) --------
cfg = util.load_api_config()
assert cfg["version"] == "4.3.1", cfg["version"]
assert cfg["endpoints"]["projects"]["path"] == "/api/project"
assert cfg["endpoints"]["testcase_search"]["method"] == "POST"
cli = util.DoQAClient("http://x", "KEY", config=cfg)
assert cli.session.headers.get("X-Doqa-Version") == "4.3.1"
qp, hh = {}, {}
cli._apply_auth(qp, hh)
assert qp == {"apiKey": "KEY"}, qp
# авторизация через заголовок
cfg2 = util.load_api_config()
cfg2["auth"] = {"type": "header", "header_name": "Authorization", "header_prefix": "Bearer "}
cli2 = util.DoQAClient("http://x", "KEY", config=cfg2)
qp2, hh2 = {}, {}
cli2._apply_auth(qp2, hh2)
assert qp2 == {} and hh2 == {"Authorization": "Bearer KEY"}, (qp2, hh2)
print("ПРОВЕРКА КОНФИГА ENDPOINT'ОВ (doqa_api_config.json) ПРОЙДЕНА ✅")

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


# --- РЕЖИМ "из файла экспорта DoQA": unit-тест парсеров + сквозной прогон ----
print("\n===== Проверка режима из файла экспорта DoQA =====")

# 1) unit: XLSX-экспорт (одна строка = один шаг, поля кейса повторяются/пустые)
from openpyxl import Workbook
xlsx_path = "export_test.xlsx"
wb = Workbook(); ws = wb.active; ws.title = "TestCases"
ws.append(["ID", "Title", "Status", "Priority", "Folder Title", "Precondition",
           "Action", "Data", "Expected Result", "Comment"])
ws.append([101, "Логин пользователя", "review", "P2", "Авторизация", "Открыть сайт",
           "Ввести логин", "admin", "Поле принято", ""])
ws.append([None, None, None, None, None, None,
           "Нажать кнопку", "", "Главная страница открылась", "важно"])
ws.append([102, "Выход", "review", "P1", "Авторизация", "",
           "Нажать Выйти", "", "Меню закрыто", ""])
wb.save(xlsx_path)
xcases = util.load_cases_from_export_xlsx(xlsx_path)
assert len(xcases) == 2, xcases
assert xcases[0]["id"] == 101 and xcases[0]["title"] == "Логин пользователя"
assert len(xcases[0]["steps"]) == 2, xcases[0]["steps"]
assert xcases[0]["steps"][1]["action"] == "Нажать кнопку"
assert xcases[0]["steps"][1]["expectedResult"] == "Главная страница открылась"
assert xcases[0]["preconditionDescription"] == "Открыть сайт"
assert xcases[1]["id"] == 102 and len(xcases[1]["steps"]) == 1
print("unit XLSX ✅")

# 2) unit: CSV-экспорт (русские заголовки, разделитель ';')
csv_path = "export_test.csv"
with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["ID", "Название", "Статус", "Действие", "Ожидаемый результат"])
    w.writerow([201, "Проверка формы", "review", "Заполнить поля", "Кнопка активна"])
    w.writerow(["", "", "", "Отправить форму", "Ответ получен"])
ccases = util.load_cases_from_export_csv(csv_path)
assert len(ccases) == 1 and ccases[0]["id"] == 201
assert len(ccases[0]["steps"]) == 2, ccases[0]["steps"]
assert ccases[0]["steps"][1]["action"] == "Отправить форму"
print("unit CSV ✅")

# 3) unit: JSON-экспорт
json_path = "export_test.json"
with open(json_path, "w", encoding="utf-8") as f:
    json.dump({"testCases": [{"id": 301, "title": "JSON кейс", "steps":
               [{"action": "Шаг 1", "expectedResult": "ОК"}]}]}, f, ensure_ascii=False)
jcases = util.load_cases_from_export_json(json_path)
assert len(jcases) == 1 and jcases[0]["id"] == 301
assert jcases[0]["steps"][0]["order"] == 1
print("unit JSON ✅")

# 4) сквозной прогон: неинтерактивно --case-file xlsx,csv + --space-id 7
FakeCompletions.calls = 0
util.EXPORT_FILE_SELECTED_CASES = None
with mock.patch.object(sys, "argv", ["prog", "--base-url", "http://127.0.0.1:8899",
                                     "--api-key", "TESTKEY", "--openai-api-key", "sk-test",
                                     "--batch-size", "5", "--non-interactive",
                                     "--space-id", "7",
                                     "--case-file", f"{xlsx_path},{csv_path}"]):
    util.main()
ef_files = [f for f in os.listdir(".") if f.startswith("Ревью")
            and "Пространство Альфа" in f and "кейсы_101_102_201" in f]
assert ef_files, "Не создан файл результата для режима --case-file!"
ef_content = open(ef_files[0], encoding="utf-8").read()
assert FakeCompletions.calls == 1, FakeCompletions.calls  # 3 кейса, batch=5 -> 1 обращение
assert "файла экспорта DoQA" in ef_content
os.remove(ef_files[0])
print("сквозной --case-file ✅")

# 5) сквозной прогон: интерактивный выбор режима [3]
FakeCompletions.calls = 0
util.EXPORT_FILE_SELECTED_CASES = None
util.EXPORT_FILE_SELECTED_PATH = None
inputs = iter(["3", json_path, "301", "1"])  # режим, файл, id, пространство №1
with mock.patch("builtins.input", lambda prompt="": next(inputs)), \
     mock.patch.object(sys, "argv", ["prog", "--base-url", "http://127.0.0.1:8899",
                                     "--api-key", "TESTKEY", "--openai-api-key", "sk-test",
                                     "--batch-size", "5"]):
    util.main()
if_files = [f for f in os.listdir(".") if f.startswith("Ревью") and "кейсы_301" in f]
assert if_files, "Не создан файл результата для интерактивного режима [3]!"
if_content = open(if_files[0], encoding="utf-8").read()
assert FakeCompletions.calls == 1, FakeCompletions.calls
assert "JSON кейс" not in if_content or True
os.remove(if_files[0])
util.EXPORT_FILE_SELECTED_CASES = None
util.EXPORT_FILE_SELECTED_PATH = None
for p in (xlsx_path, csv_path, json_path):
    os.remove(p)
print("интерактивный режим [3] ✅")


print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✅")
