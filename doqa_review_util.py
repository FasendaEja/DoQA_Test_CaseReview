#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Утилита ревью тест-кейсов DoQA с помощью ИИ.

Шаги:
 1) Получение списка проектов (пространств) и их id из DoQA.
 2) Получение списка создателей кейсов и ответственных.
 3) Интерактивный выбор фильтров: пространство, дата обновления, автор.
 4) Получение списка тест-кейсов по фильтрам:
      spaceId       - выбрано на шаге 3
      statuses      - ["review"]
      updatedDate*  - выбрано на шаге 3
      creatorIds    - выбрано на шаге 3
 5) Передача полученных кейсов + правил из файла "DoQA_test_case_review_rules_" в ИИ
    с промтом (промт хранится в текстовом файле и перечитывается при КАЖДОМ обращении к ИИ).
 6) Результат ИИ сохраняется в файл вида:
    "Ревью<текущая дата и время> кейсов из пространства <пространство> c автором creatorIds"

Настройки (через переменные окружения или .env):
  DOQA_BASE_URL   - адрес инстанса DoQA, например https://doqa.mycompany.ru
  DOQA_API_KEY    - API-ключ DoQA (подставляется как параметр apiKey)
  OPENAI_API_KEY  - ключ ИИ (OpenAI-совместимый API)
  OPENAI_BASE_URL - (опционально) свой base_url, например для локальной модели
  OPENAI_MODEL    - (опционально) имя модели, по умолчанию gpt-4o-mini
"""

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import datetime, date, timedelta

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# ---------------------------------------------------------------------------
# Константы / имена файлов по требованиям
# ---------------------------------------------------------------------------
RULES_FILE_NAME = "DoQA_test_case_review_rules_"          # файл с правилами проверки
PROMPT_FILE_NAME = "prompt.txt"                            # файл с промтом (перечитывается каждый раз)
DEFAULT_STATUS_FILTER = ["review"]                         # фильтр по статусу из требования

DEFAULT_DOQA_BASE_URL = os.getenv("DOQA_BASE_URL", "")
DOQA_API_KEY = os.getenv("DOQA_API_KEY", "")


# ===========================================================================
# 1-2. Клиент DoQA
# ===========================================================================
class DoQAClient:
    """Минимальный клиент REST API DoQA."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()

    # -- низкоуровневый запрос ----------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        p = dict(params or {})
        if self.api_key:
            p.setdefault("apiKey", self.api_key)
        resp = self.session.get(url, params=p, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(
                f"DoQA API error {resp.status_code} при запросе {url}: {resp.text[:500]}"
            )
        return resp.json()

    @staticmethod
    def _extract_items(payload, *keys):
        """DoQA может возвращать либо массив, либо объект со списком по ключу."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for k in keys:
                v = payload.get(k)
                if isinstance(v, list):
                    return v
            data = payload.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for k in keys:
                    v = data.get(k)
                    if isinstance(v, list):
                        return v
        return []

    # -- шаг 1: список проектов (пространств) и их id ------------------------
    def get_projects(self) -> list[dict]:
        raw = self._extract_items(self._get("/api/project"), "projects")
        projects = []
        for prj in raw:
            projects.append({
                "id": prj.get("id"),
                "name": prj.get("name") or prj.get("title") or f"Проект {prj.get('id')}",
            })
        projects.sort(key=lambda x: str(x["name"]))
        return projects

    # -- шаг 2: пользователи (создатели кейсов и ответственные) --------------
    def get_users(self) -> list[dict]:
        raw = self._extract_items(self._get("/api/user"), "users")
        users = []
        for u in raw:
            users.append({
                "id": u.get("id"),
                "name": (u.get("name") or "").strip()
                        or f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                        or f"Пользователь {u.get('id')}",
                "email": u.get("email", ""),
            })
        users.sort(key=lambda x: str(x["name"]).lower())
        return users

    # -- шаг 4: поиск тест-кейсов по фильтрам --------------------------------
    def search_test_cases(self, space_id, statuses, creator_ids,
                          updated_after=None, updated_before=None) -> list[dict]:
        body = {
            "spaceId": int(space_id),
            "statuses": statuses,
            "creatorIds": [int(c) for c in creator_ids],
        }
        if updated_after:
            body["updatedDateFrom"] = updated_after
        if updated_before:
            body["updatedDateTo"] = updated_before

        url = f"{self.base_url}/api/testcase/search"
        params = {"apiKey": self.api_key} if self.api_key else {}
        all_cases: list[dict] = []
        page = 1
        while True:
            params["page"] = page
            params["limit"] = 100
            resp = self.session.post(url, params=params, json=body, timeout=120)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"DoQA API error {resp.status_code} при запросе {url}: {resp.text[:500]}"
                )
            items = self._extract_items(resp.json(), "testCases", "items", "content")
            if not items:
                break
            all_cases.extend(items)
            if len(items) < 100:
                break
            page += 1
        return all_cases

    # -- получение отдельного тест-кейса по id --------------------------------
    def get_test_case(self, case_id) -> dict | None:
        """Возвращает тест-кейс по его id или None, если не найден.

        Пробует типовой REST DoQA: GET /api/testcase/{id}. Если ответ — объект
        со списком, берётся первый элемент.
        """
        try:
            payload = self._get(f"/api/testcase/{int(case_id)}")
        except (RuntimeError, requests.RequestException):
            return None
        # Прямой объект кейса
        if isinstance(payload, dict) and (payload.get("id") is not None
                                          or "title" in payload or "steps" in payload):
            return payload
        items = self._extract_items(payload, "testCases", "items", "content")
        if items:
            first = items[0]
            if isinstance(first, dict):
                return first
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                return data
        return None


# ===========================================================================
# Загрузка правил и промта
# ===========================================================================
def find_rules_file(start_dir: str = ".") -> str:
    """Ищет файл с названием 'DoQA_test_case_review_rules_' (с любым расширением)."""
    candidates = []
    for root, _dirs, files in os.walk(start_dir):
        for fn in files:
            if fn.startswith(RULES_FILE_NAME):
                candidates.append(os.path.join(root, fn))
    if not candidates:
        raise FileNotFoundError(
            f"Не найден файл правил с именем '{RULES_FILE_NAME}...'. "
            f"Положите его рядом с утилитой."
        )
    candidates.sort()
    return candidates[0]


def load_rules(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_prompt(prompt_path: str) -> str:
    """Читает промт из текстового файла. Вызывается ПЕРЕД КАЖДЫМ обращением к ИИ,
    поэтому пользователь может отредактировать промт в любой момент."""
    with open(prompt_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        raise ValueError(f"Файл промта '{prompt_path}' пуст.")
    return text


DEFAULT_PROMPT = (
    "Проверь тест-кейсы на соответствие правилам из файла \"DoQA_test_case_review_rules_\" "
    "и правилам русского языка.\n"
    "Результат выдавай по каждому кейсу отдельно. Явно указывай в каком шаге ошибка."
)


# ===========================================================================
# Форматирование тест-кейсов для ИИ
# ===========================================================================
def format_step(s: dict, idx: int) -> str:
    parts = [f"  Шаг {s.get('order', idx)}:"]
    action = s.get("action") or s.get("step") or ""
    expected = s.get("expectedResult") or s.get("expected") or ""
    data = s.get("data") or s.get("preconditions") or ""
    if data:
        parts.append(f"    Данные: {data}")
    if action:
        parts.append(f"    Действие: {action}")
    if expected:
        parts.append(f"    Ожидаемый результат: {expected}")
    comment = s.get("comment") or ""
    if comment:
        parts.append(f"    Комментарий: {comment}")
    return "\n".join(parts)


def format_test_case_for_llm(tc: dict) -> str:
    tc_id = tc.get("id")
    title = tc.get("title") or tc.get("name") or "(без названия)"
    status = tc.get("status") or ""
    priority = tc.get("priority") or ""
    layer = tc.get("layer") or ""
    folder = tc.get("folderTitle") or tc.get("folder") or ""
    precond = tc.get("preconditionDescription") or tc.get("preconditions") or ""
    desc = tc.get("description") or ""

    lines = [
        f"### Тест-кейс ID={tc_id}: {title}",
        f"Статус: {status}; Приоритет: {priority}; Уровень: {layer}; Папка: {folder}",
    ]
    if desc:
        lines.append(f"Описание: {desc}")
    if precond:
        lines.append(f"Предусловия: {precond}")
    steps = tc.get("steps") or []
    if steps:
        lines.append("Шаги:")
        for i, s in enumerate(steps, 1):
            lines.append(format_step(s, i))
    postcond = tc.get("postconditionDescription") or ""
    if postcond:
        lines.append(f"Постусловия: {postcond}")
    return "\n".join(lines)


# ===========================================================================
# 5. Клиент ИИ
# ===========================================================================
class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        if OpenAI is None:
            raise RuntimeError("Не установлен пакет openai. Установите: pip install openai")
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model

    def analyze(self, prompt: str, rules: str, cases_block: str) -> str:
        system = (
            "Ты — опытный QA-аналитик. Тебе даны правила оформления тест-кейсов "
            "и сами тест-кейсы. Строго следуй промту пользователя."
        )
        user_msg = (
            f"=== ПРАВИЛА ПРОВЕРКИ (файл \"{RULES_FILE_NAME}\") ===\n{rules}\n\n"
            f"=== ТЕСТ-КЕЙСЫ ДЛЯ АНАЛИЗА ===\n{cases_block}"
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"{prompt}\n\n{user_msg}"},
            ],
            temperature=0.2,
        )
        return resp.choices[0].message.content or "(пустой ответ модели)"


# ===========================================================================
# 3. Интерактивный выбор фильтров
# ===========================================================================
def choose_space(projects: list[dict]) -> dict:
    print("\n=== Шаг 1-3. Выбор пространства (проекта) ===")
    if not projects:
        raise RuntimeError("DoQA вернул пустой список проектов.")
    for i, p in enumerate(projects, 1):
        print(f"  [{i}] {p['name']} (id={p['id']})")
    while True:
        sel = input(f"Выберите пространство [1-{len(projects)}]: ").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(projects):
            return projects[int(sel) - 1]
        print("Некорректный ввод, попробуйте ещё раз.")


def parse_date_input(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value.lower() == "today":
        return date.today().isoformat()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("Дата должна быть в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД")


def choose_updated_dates() -> tuple[str | None, str | None]:
    print("\n=== Шаг 3. Фильтр по дате обновления ===")
    print("Формат: ДД.ММ.ГГГГ (или 'today'), пустая строка — не фильтровать.")
    while True:
        try:
            after = parse_date_input(input("Обновлены ПОСЛЕ (включительно) [enter = без фильтра]: "))
            before = parse_date_input(input("Обновлены ДО (включительно)   [enter = без фильтра]: "))
            return after, before
        except ValueError as e:
            print(f"Ошибка: {e}. Попробуйте ещё раз.")


def choose_creators(users: list[dict]) -> tuple[list[dict], list[int]]:
    print("\n=== Шаг 2-3. Выбор авторов (создателей кейсов) ===")
    print("Доступные пользователи (создатели кейсов и ответственные):")
    for i, u in enumerate(users, 1):
        email = f" <{u['email']}>" if u["email"] else ""
        print(f"  [{i}] {u['name']}{email} (id={u['id']})")
    print("Можно указать несколько id через запятую, 'all' — все, '0' — без фильтра по автору.")
    while True:
        sel = input("Выберите авторов: ").strip()
        if sel == "":
            continue
        if sel == "0":
            return [], []
        if sel.lower() == "all":
            return users, [u["id"] for u in users]
        try:
            idxs = [int(x) for x in re.split(r"[,;\s]+", sel) if x]
            chosen = []
            for ix in idxs:
                if 1 <= ix <= len(users):
                    chosen.append(users[ix - 1])
                else:
                    raise IndexError
            if chosen:
                uniq = {u["id"]: u for u in chosen}
                return list(uniq.values()), list(uniq.keys())
        except (ValueError, IndexError):
            pass
        print("Некорректный ввод, попробуйте ещё раз.")


# ===========================================================================
# Режим "ревью по списку ID"
# ===========================================================================
def parse_case_ids(text: str) -> list[int]:
    """Разбирает строку/файл с id кейсов: числа через запятую, пробелы, ; или построчно."""
    ids: list[int] = []
    seen: set[int] = set()
    for token in re.split(r"[,;\s]+", text):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f"Некорректный id тест-кейса: '{token}'")
        val = int(token)
        if val not in seen:
            seen.add(val)
            ids.append(val)
    return ids


def read_case_ids_file(path: str) -> list[int]:
    """Читает id из текстового файла (одна строка или построчно)."""
    with open(path, encoding="utf-8-sig") as f:
        return parse_case_ids(f.read())


def collect_cases_by_ids(doa: DoQAClient, case_ids: list[int]) -> tuple[list[dict], list[int]]:
    """Загружает кейсы по id. Возвращает (найденные кейсы, список ненайденных id)."""
    found: list[dict] = []
    missing: list[int] = []
    for cid in case_ids:
        tc = doa.get_test_case(cid)
        if tc is None:
            missing.append(cid)
        else:
            if tc.get("id") is None:
                tc["id"] = cid
            found.append(tc)
    return found, missing


def choose_mode_interactive() -> tuple[str, list[int]]:
    """Интерактивный выбор режима.

    Возвращает (режим, id_кейсов):
      ("filters", [])  — обычный режим по фильтрам;
      ("ids", [...])   — режим списка кейсов по id;
      ("file", [...])  — кейсы взяты из файла экспорта DoQA
                         (сами кейсы кладутся в EXPORT_FILE_SELECTED_CASES).
    """
    print("\nДоступные режимы:")
    print("  [1] Ревью по фильтрам (пространство, дата обновления, автор)")
    print("  [2] Ревью конкретного списка кейсов по id")
    print("  [3] Ревью кейсов из файла экспорта DoQA (.xlsx/.csv/.json)")
    while True:
        sel = input("Выберите режим [1/2/3] (по умолчанию 1): ").strip() or "1"
        if sel == "1":
            return "filters", []
        if sel == "2":
            return "ids", ask_case_ids_interactive()
        if sel == "3":
            path = input("Укажите путь к файлу экспорта DoQA (CSV/XLSX/JSON): ").strip()
            if not path:
                print("Пустой путь, возвращаемся к выбору режима.")
                continue
            try:
                cases = load_cases_from_export_file(path)
            except Exception as e:
                print(f"Ошибка чтения файла '{path}': {e}. Попробуйте ещё раз.")
                continue
            if not cases:
                print(f"В файле '{path}' не найдено ни одного тест-кейса.")
                continue
            print(f"[OK] Прочитано тест-кейсов из файла '{path}': {len(cases)}")
            for tc in cases:
                print(f"  - ID={_case_id_of(tc)}: {_case_title_of(tc)}")
            ids_input = input(
                "id кейсов из файла для ревью через запятую [enter = все]: ").strip().lower()
            if ids_input in ("", "all"):
                chosen = cases
            else:
                wanted = set(parse_case_ids(ids_input))
                chosen = [tc for tc in cases if _case_id_of(tc) in wanted]
                missed = sorted(wanted - {_case_id_of(tc) for tc in chosen})
                if missed:
                    print(f"ВНИМАНИЕ: в файле не найдены id: {missed}")
            if not chosen:
                print("В файле нет кейсов с указанными id. Возврат к выбору режима.")
                continue
            global EXPORT_FILE_SELECTED_CASES, EXPORT_FILE_SELECTED_PATH
            EXPORT_FILE_SELECTED_CASES = chosen
            EXPORT_FILE_SELECTED_PATH = path
            return "file", [_case_id_of(tc) for tc in chosen]
        print("Некорректный ввод, попробуйте ещё раз.")


def ask_case_ids_interactive() -> list[int]:
    print("Введите id тест-кейсов через запятую/пробел (один id — тоже список),")
    print("или путь к файлу со списком id:")
    while True:
        raw = input("> ").strip()
        if not raw:
            print("Пустой ввод, попробуйте ещё раз.")
            continue
        try:
            if os.path.isfile(raw):
                ids = read_case_ids_file(raw)
                print(f"Прочитано id из файла '{raw}': {ids}")
            else:
                ids = parse_case_ids(raw)
            if ids:
                return ids
            print("Список пуст, попробуйте ещё раз.")
        except ValueError as e:
            print(f"Ошибка: {e}. Попробуйте ещё раз.")


# ===========================================================================
# Режим "ревью кейсов из файла экспорта DoQA"
# ===========================================================================
# При выборе этого режима в интерактивном меню main() забирает кейсы отсюда.
EXPORT_FILE_SELECTED_CASES = None
EXPORT_FILE_SELECTED_PATH = None

# Варианты названий колонок в экспорте DoQA (EN + RU), lowercase
_EXPORT_ID_KEYS = ("id", "test case id", "testcase id", "tc id", "case id",
                   "номер", "id кейса", "идентификатор")
_EXPORT_TITLE_KEYS = ("title", "name", "summary", "название", "заголовок", "имя")
_EXPORT_STATUS_KEYS = ("status", "статус")
_EXPORT_PRIORITY_KEYS = ("priority", "приоритет")
_EXPORT_LAYER_KEYS = ("layer", "уровень")
_EXPORT_FOLDER_KEYS = ("folder", "folder title", "папка", "директория")
_EXPORT_DESC_KEYS = ("description", "описание")
_EXPORT_PRECOND_KEYS = ("precondition", "preconditions", "precondition description",
                        "предусловие", "предусловия")
_EXPORT_POSTCOND_KEYS = ("postcondition", "postconditions", "postcondition description",
                         "постусловие", "постусловия")
_EXPORT_STEP_ORDER_KEYS = ("step id", "step number", "шаг", "порядок шага", "№ шага")
_EXPORT_STEP_ACTION_KEYS = ("action", "step", "step description", "operation",
                            "действие", "шаг (действие)", "описание шага")
_EXPORT_STEP_DATA_KEYS = ("data", "test data", "step data", "данные", "тестовые данные")
_EXPORT_STEP_EXPECTED_KEYS = ("expected result", "expected", "result",
                              "ожидаемый результат", "ожидание")
_EXPORT_STEP_COMMENT_KEYS = ("comment", "step comment", "комментарий")


def _norm_key(k) -> str:
    return str(k).strip().lower().replace("\ufeff", "")


def _pick(row: dict, keys: tuple[str, ...]) -> str | None:
    """Возвращает значение первой непустой колонки из списка возможных имён."""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _normalize_case_id(tc: dict):
    """Числовые id приводим к int (в CSV/XLSX все значения — строки)."""
    v = tc.get("id")
    if isinstance(v, str) and v.strip().isdigit():
        tc["id"] = int(v.strip())
    return tc


def _make_case_shell(tc_id) -> dict:
    return {"id": tc_id, "steps": []}


def _case_id_of(tc: dict):
    v = tc.get("id")
    try:
        return int(str(v).strip())
    except (ValueError, TypeError, AttributeError):
        return v


def _case_title_of(tc: dict) -> str:
    return str(tc.get("title") or tc.get("name") or "(без названия)")


def _rows_to_cases(rows: list[dict]) -> list[dict]:
    """Собирает тест-кейсы из плоских строк (формат табличного экспорта DoQA:
    одна строка = один шаг кейса; поля кейса либо повторяются в каждой строке,
    либо заполнены только в первой строке кейса, а id в строках-продолжениях
    может отсутствовать)."""
    cases: list[dict] = []
    index: dict = {}  # id кейса -> позиция в cases
    last_case: dict | None = None       # предыдущая обработанная строка-кейс
    last_step_values: dict | None = None  # значения шага предыдущей строки

    for raw_row in rows:
        row = {_norm_key(k): v for k, v in raw_row.items() if k is not None}
        has_any = any(v is not None and str(v).strip() for v in row.values())
        if not has_any:
            continue
        rid = _pick(row, _EXPORT_ID_KEYS)
        title = _pick(row, _EXPORT_TITLE_KEYS)
        case_fields = {}
        for keys, field in ((_EXPORT_STATUS_KEYS, "status"),
                            (_EXPORT_PRIORITY_KEYS, "priority"),
                            (_EXPORT_LAYER_KEYS, "layer"),
                            (_EXPORT_FOLDER_KEYS, "folderTitle"),
                            (_EXPORT_DESC_KEYS, "description"),
                            (_EXPORT_PRECOND_KEYS, "preconditionDescription"),
                            (_EXPORT_POSTCOND_KEYS, "postconditionDescription")):
            v = _pick(row, keys)
            if v:
                case_fields[field] = v

        action = _pick(row, _EXPORT_STEP_ACTION_KEYS)
        expected = _pick(row, _EXPORT_STEP_EXPECTED_KEYS)
        data = _pick(row, _EXPORT_STEP_DATA_KEYS)
        comment = _pick(row, _EXPORT_STEP_COMMENT_KEYS)
        order_raw = _pick(row, _EXPORT_STEP_ORDER_KEYS)
        step_values = {"action": action, "expected result": expected,
                       "data": data, "comment": comment}
        has_step = any(x is not None for x in (action, expected, data, comment))

        # --- определяем: новая строка кейса или продолжение предыдущего ------
        is_new = True
        if rid is not None and rid in index:
            is_new = False          # повтор полей того же кейса
        elif rid is None and last_case is not None:
            if last_case.get("id") is None:
                is_new = False      # цепочка строк без id
            elif not case_fields and not title:
                # пустые поля кейса => строка-продолжение (шаг) предыдущего кейса
                is_new = False
        if rid is None and not is_new and not has_step and last_step_values:
            # строка-продолжение без собственного шага — берём шаг из предыдущей
            step_values = dict(last_step_values)
            has_step = True

        if is_new:
            c = _make_case_shell(rid)
            if rid is not None:
                index[rid] = len(cases)
            if title:
                c["title"] = title
            c.update(case_fields)
            cases.append(c)
        else:
            c = cases[-1]
            if rid is not None and c.get("id") is None:
                c["id"] = rid
                index[rid] = len(cases) - 1
            if title and not c.get("title"):
                c["title"] = title
            for f, v in case_fields.items():
                c.setdefault(f, v)

        if has_step:
            step: dict = {}
            if order_raw is not None:
                digits = re.sub(r"\D", "", order_raw)
                if digits:
                    step["order"] = int(digits)
            if step_values["action"]:
                step["action"] = step_values["action"]
            if step_values["expected result"]:
                step["expectedResult"] = step_values["expected result"]
            if step_values["data"]:
                step["data"] = step_values["data"]
            if step_values["comment"]:
                step["comment"] = step_values["comment"]
            c["steps"].append(step)
        last_case = c
        last_step_values = step_values

    # нормализация: id для кейсов без id, порядок шагов
    norm: list[dict] = []
    for c in cases:
        if c.get("id") is None:
            if not c["steps"] and not c.get("title"):
                continue
            c["id"] = f"файл-{len(norm) + 1}"
        for i, s in enumerate(c["steps"], 1):
            s.setdefault("order", i)
        norm.append(_normalize_case_id(c))
    return norm


def load_cases_from_export_csv(path: str) -> list[dict]:
    """Читает CSV-экспорт DoQA (автоопределение разделителя ;/,/табуляции)."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        text = f.read()
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        if text.count(";") > text.count(","):
            class _Semicolon(csv.excel):
                delimiter = ";"
            dialect = _Semicolon()
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    return _rows_to_cases(rows)


def load_cases_from_export_xlsx(path: str) -> list[dict]:
    """Читает XLSX-экспорт DoQA (основной формат экспорта тест-кейсов)."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("Для чтения XLSX нужен пакет openpyxl: pip install openpyxl")
    wb = load_workbook(path, read_only=True, data_only=True)
    rows: list[dict] = []
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        if not header:
            continue
        header = [str(h).strip() if h is not None else "" for h in header]
        for values in it:
            if values is None:
                continue
            row = {header[i]: values[i] for i in range(min(len(header), len(values)))}
            if any(v is not None and str(v).strip() for v in row.values()):
                rows.append(row)
        break  # первый лист = список тест-кейсов
    wb.close()
    return _rows_to_cases(rows)


def load_cases_from_export_json(path: str) -> list[dict]:
    """Читает JSON-экспорт DoQA: массив кейсов либо объект {testCases|items|data: [...]}."""
    with open(path, "r", encoding="utf-8-sig") as f:
        payload = json.load(f)
    items = DoQAClient._extract_items(payload, "testCases", "items", "content")
    cases: list[dict] = []
    for i, tc in enumerate(items, 1):
        if not isinstance(tc, dict):
            continue
        if tc.get("id") is None:
            tc["id"] = f"файл-{i}"
        steps = tc.get("steps")
        if isinstance(steps, list):
            for j, s in enumerate(steps, 1):
                if isinstance(s, dict):
                    s.setdefault("order", j)
        cases.append(tc)
    return cases


def load_cases_from_export_file(path: str) -> list[dict]:
    """Загружает тест-кейсы из файла экспорта DoQA по расширению (.xlsx/.csv/.json)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Файл не найден: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        return load_cases_from_export_xlsx(path)
    if ext == ".csv":
        return load_cases_from_export_csv(path)
    if ext == ".json":
        return load_cases_from_export_json(path)
    raise ValueError(
        f"Неизвестный формат файла '{os.path.basename(path)}'. "
        "Поддерживаются форматы экспорта DoQA: .xlsx, .csv, .json")


# ===========================================================================
# 6. Формирование имени файла результата
# ===========================================================================
def make_output_filename(space_name: str, creators: list[dict],
                         case_ids: list[int] | None = None) -> str:
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_space = re.sub(r'[\\/:*?"<>|]', "-", space_name)
    if case_ids:
        # Режим "список кейсов по id": в имени указываем сами id кейсов
        authors = "кейсы_" + "_".join(str(i) for i in case_ids)
        return f"Ревью{now} кейсов из пространства {safe_space} c автором {authors}.md"
    if creators:
        authors = "_".join(str(u["id"]) for u in creators)
    else:
        authors = "все авторы"
    return f"Ревью{now} кейсов из пространства {safe_space} c автором {authors}.md"


# ===========================================================================
# Main
# ===========================================================================
def main():
    # Корректный вывод кириллицы в консоли Windows (cmd/PowerShell)
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="ИИ-ревью тест-кейсов DoQA")
    ap.add_argument("--base-url", default=DEFAULT_DOQA_BASE_URL,
                    help="URL инстанса DoQA (или env DOQA_BASE_URL)")
    ap.add_argument("--api-key", default=DOQA_API_KEY,
                    help="API-ключ DoQA (или env DOQA_API_KEY)")
    ap.add_argument("--openai-api-key", default=os.getenv("OPENAI_API_KEY", ""),
                    help="Ключ ИИ (или env OPENAI_API_KEY)")
    ap.add_argument("--openai-base-url", default=os.getenv("OPENAI_BASE_URL", "") or None,
                    help="Свой base_url для ИИ (или env OPENAI_BASE_URL)")
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    help="Модель ИИ (или env OPENAI_MODEL)")
    ap.add_argument("--batch-size", type=int, default=5,
                    help="Сколько кейсов отправлять в ИИ за одно обращение (по умолчанию 5)")
    ap.add_argument("--non-interactive", action="store_true",
                    help="Без вопросов: использовать параметры из аргументов ниже")
    ap.add_argument("--space-id", type=int, help="spaceId (для --non-interactive)")
    ap.add_argument("--creator-ids", help="creatorIds через запятую (для --non-interactive)")
    ap.add_argument("--updated-after", help="дата 'не раньше' ДД.ММ.ГГГГ (для --non-interactive)")
    ap.add_argument("--updated-before", help="дата 'не позже' ДД.ММ.ГГГГ (для --non-interactive)")
    ap.add_argument("--case-ids",
                    help="Ревью конкретного списка кейсов по id: "
                         "числа через запятую/пробел ('123' или '123,456,789') "
                         "или путь к файлу со списком id")
    ap.add_argument("--case-file",
                    help="Ревью кейсов из файла экспорта DoQA "
                         "(.xlsx — основной формат экспорта, также .csv/.json). "
                         "Опционально можно указать несколько файлов через запятую")
    args = ap.parse_args()

    if not args.base_url:
        print("Укажите URL DoQA: --base-url или переменная окружения DOQA_BASE_URL")
        sys.exit(1)

    # --- подготовка файлов правил и промта -------------------------------
    rules_path = find_rules_file(".")
    print(f"[OK] Найден файл правил: {rules_path}")
    rules = load_rules(rules_path)

    prompt_path = os.path.join(".", PROMPT_FILE_NAME)
    if not os.path.exists(prompt_path):
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_PROMPT + "\n")
        print(f"[OK] Создан файл промта: {prompt_path} (можно отредактировать — "
              f"он перечитывается перед каждым обращением к ИИ)")

    doqa = DoQAClient(args.base_url, args.api_key)

    # --- шаг 1: проекты ----------------------------------------------------
    print("\n[1/6] Получение списка проектов (пространств) из DoQA...")
    projects = doqa.get_projects()
    print(f"      Найдено проектов: {len(projects)}")

    # --- шаг 2: пользователи ----------------------------------------------
    print("[2/6] Получение списка создателей кейсов и ответственных...")
    users = doqa.get_users()
    print(f"      Найдено пользователей: {len(users)}")

    # --- выбор режима: по фильтрам / по id / из файла экспорта DoQA --------
    mode = "filters"
    mode_ids: list[int] = []
    export_path: str | None = None
    if args.case_file and args.case_ids:
        print("Нельзя одновременно указать --case-file и --case-ids.")
        sys.exit(1)
    if args.case_file:
        mode = "file"
        export_path = args.case_file
    elif args.case_ids:
        mode = "ids"
        try:
            if os.path.isfile(args.case_ids):
                mode_ids = read_case_ids_file(args.case_ids)
            else:
                mode_ids = parse_case_ids(args.case_ids)
        except ValueError as e:
            print(f"Ошибка разбора --case-ids: {e}")
            sys.exit(1)
        if not mode_ids:
            print("Список id (--case-ids) пуст.")
            sys.exit(1)
    elif not args.non_interactive:
        mode, mode_ids = choose_mode_interactive()
        if mode == "file":
            export_path = EXPORT_FILE_SELECTED_PATH

    if mode == "file":
        # ------- Режим "кейсы из файла экспорта DoQA" -----------------------
        if EXPORT_FILE_SELECTED_CASES is not None:
            cases = EXPORT_FILE_SELECTED_CASES  # выбраны интерактивно
        else:
            all_file_cases = []
            for pth in re.split(r"[;,]", export_path):
                pth = pth.strip()
                if not pth:
                    continue
                try:
                    fc = load_cases_from_export_file(pth)
                except Exception as e:
                    print(f"Ошибка чтения файла экспорта '{pth}': {e}")
                    sys.exit(1)
                print(f"[OK] Прочитано тест-кейсов из файла '{pth}': {len(fc)}")
                all_file_cases.extend(fc)
            if not all_file_cases:
                print(f"В файле '{export_path}' не найдено ни одного тест-кейса.")
                sys.exit(1)
            for tc in all_file_cases:
                print(f"  - ID={_case_id_of(tc)}: {_case_title_of(tc)}")
            cases = all_file_cases
        print(f"\nРежим: ревью кейсов из файла экспорта DoQA: {export_path}")

        # пространство нужно только для имени файла результата
        if args.space_id:
            space = next((p for p in projects if p["id"] == args.space_id), None)
            if space is None:
                print(f"Пространство с spaceId={args.space_id} не найдено.")
                sys.exit(1)
        elif len(projects) == 1:
            space = projects[0]
            print(f"Пространство выбрано автоматически: '{space['name']}' (id={space['id']})")
        else:
            space = choose_space(projects)
        creators, cid_list, upd_after, upd_before = [], [], None, None
        missing = []
        out_case_ids = [_case_id_of(tc) for tc in cases]
        print(f"Пространство (для имени файла): '{space['name']}' (id={space['id']})")
        print(f"[4/6] Кейсы получены из файла, количество: {len(cases)}")
    elif mode == "ids":
        # ------- Режим "список кейсов по id": шаги 3-4 сводятся к выбору ----
        # ------- пространства (для имени файла) и загрузки кейсов по id -----
        if args.non_interactive or args.space_id:
            space = next((p for p in projects if p["id"] == args.space_id), None)
            if space is None:
                print(f"Пространство с spaceId={args.space_id} не найдено.")
                sys.exit(1)
        elif len(projects) == 1:
            space = projects[0]
            print(f"\nПространство выбрано автоматически: '{space['name']}' (id={space['id']})")
        else:
            space = choose_space(projects)

        creators, cid_list, upd_after, upd_before = [], [], None, None

        print(f"\nРежим: ревью конкретного списка кейсов по id: {mode_ids}")
        print(f"Пространство: '{space['name']}' (id={space['id']})")

        print("[4/6] Получение тест-кейсов по id...")
        cases, missing = collect_cases_by_ids(doa=doqa, case_ids=mode_ids)
        if missing:
            print(f"      ВНИМАНИЕ: не найдены кейсы с id: {missing}")
        print(f"      Получено тест-кейсов: {len(cases)}")
        if not cases:
            print("Ни один кейс из списка не найден. Завершение.")
            sys.exit(0)
        out_case_ids = [tc.get("id") for tc in cases]
    else:
        # ------- Обычный режим: ревью по фильтрам (шаги 3-4) ---------------
        if args.non_interactive:
            space = next((p for p in projects if p["id"] == args.space_id), None)
            if space is None:
                print(f"Пространство с spaceId={args.space_id} не найдено.")
                sys.exit(1)
            cid_list = [int(x) for x in re.split(r"[,;]", args.creator_ids or "") if x.strip()]
            creators = [u for u in users if u["id"] in cid_list]
            upd_after, upd_before = args.updated_after, args.updated_before
        else:
            space = choose_space(projects)
            upd_after, upd_before = choose_updated_dates()
            creators, cid_list = choose_creators(users)

        print(f"\nФильтры: пространство='{space['name']}' (id={space['id']}), "
              f"statuses={DEFAULT_STATUS_FILTER}, "
              f"updatedDate: от {upd_after or '—'} до {upd_before or '—'}, "
              f"creatorIds={cid_list or 'без фильтра'}")

        # --- шаг 4: получение тест-кейсов ----------------------------------
        print("[4/6] Получение списка тест-кейсов по фильтрам...")
        cases = doqa.search_test_cases(
            space_id=space["id"],
            statuses=DEFAULT_STATUS_FILTER,
            creator_ids=cid_list,
            updated_after=upd_after,
            updated_before=upd_before,
        )
        print(f"      Найдено тест-кейсов: {len(cases)}")
        if not cases:
            print("Нет кейсов по заданным фильтрам. Завершение.")
            sys.exit(0)
        out_case_ids = None

    # --- шаги 5-6: анализ ИИ и запись результата ---------------------------
    llm = LLMClient(args.openai_api_key, args.model, args.openai_base_url)
    out_name = make_output_filename(space["name"], creators, case_ids=out_case_ids)
    print(f"[5/6] Анализ тест-кейсов с помощью ИИ (модель: {args.model})...")

    batches = [cases[i:i + args.batch_size] for i in range(0, len(cases), args.batch_size)]
    with open(out_name, "w", encoding="utf-8") as out:
        if mode == "file":
            mode_desc = (
                f"- Режим: ревью кейсов из файла экспорта DoQA: {export_path}\n"
                f"- Кейсы (id): {out_case_ids}\n"
            )
        elif out_case_ids is not None:
            mode_desc = (
                f"- Режим: ревью конкретного списка кейсов по id: {out_case_ids}\n"
                + (f"- Не найдены id: {missing}\n" if missing else "")
            )
        else:
            mode_desc = (
                f"- Статусы: {DEFAULT_STATUS_FILTER}\n"
                f"- Дата обновления: от {upd_after or '—'} до {upd_before or '—'}\n"
                f"- Авторы (creatorIds): "
                + (", ".join("{} (id={})".format(u["name"], u["id"]) for u in creators) or "все")
                + "\n"
            )
        header = (
            f"# Ревью тест-кейсов\n\n"
            f"- Дата/время генерации: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"- Пространство: {space['name']} (spaceId={space['id']})\n"
            f"{mode_desc}"
            f"- Файл правил: {rules_path}\n"
            f"- Количество кейсов: {len(cases)}\n\n---\n\n"
        )
        out.write(header)

        for bi, batch in enumerate(batches, 1):
            print(f"      Партия {bi}/{len(batches)} ({len(batch)} кейсов)...")
            # Промт считывается из файла ПЕРЕД КАЖДЫМ обращением к ИИ:
            prompt = load_prompt(prompt_path)
            cases_block = "\n\n".join(format_test_case_for_llm(tc) for tc in batch)
            try:
                answer = llm.analyze(prompt, rules, cases_block)
            except Exception as e:
                answer = f"!!! Ошибка обращения к ИИ: {e}"
            ids = ", ".join(str(tc.get("id")) for tc in batch)
            out.write(f"## Партия {bi} (кейсы: {ids})\n\n{answer}\n\n---\n\n")
            out.flush()

    print(f"[6/6] Готово! Результат сохранён в файл: {out_name}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        sys.exit(130)
