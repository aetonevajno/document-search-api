# document-search-api

Асинхронный API для импорта, поиска и удаления документов. Полные документы
хранятся в PostgreSQL, полнотекстовый поиск выполняется через Elasticsearch.

## Стек

- Python 3.12–3.14, FastAPI, Pydantic;
- PostgreSQL 17, SQLAlchemy, Alembic;
- Elasticsearch 8.19;
- pytest, Ruff, mypy;
- Docker Compose для локального окружения.

## Быстрый запуск

```bash
cp .env.example .env
docker compose up --build --wait
```

После запуска доступны:

- API: <http://127.0.0.1:8000>;
- Swagger UI: <http://127.0.0.1:8000/docs>;
- OpenAPI: <http://127.0.0.1:8000/openapi.json>;
- Elasticsearch: <http://127.0.0.1:9200>.

Остановка сохраняет данные в Docker volumes:

```bash
docker compose down
```

Для полного сброса локальных данных используйте `docker compose down --volumes`.
Опубликованные порты, тестовый пароль PostgreSQL и Elasticsearch без
авторизации рассчитаны только на локальную разработку.

## Импорт CSV

Файл должен содержать колонки `text`, `created_date` и `rubrics`. Дата ожидается
в формате `YYYY-MM-DD HH:MM:SS`, а `rubrics` — в виде Python-списка строк,
например `['новости', 'космос']`.

```bash
import-documents ./posts.csv
# или
python -m app.importers ./posts.csv
```

Запуск через Docker:

```bash
docker compose run --rm \
  --volume "$PWD/posts.csv:/data/posts.csv:ro" \
  api import-documents /data/posts.csv
```

ID документа — детерминированный UUIDv5 от текста, даты и рубрик. Поэтому
повторный импорт того же файла не создаёт дубликаты. Данные пишутся пакетами:
сначала коммит в PostgreSQL, затем обновление Elasticsearch. Если индексация
прервалась, импорт можно безопасно запустить ещё раз.

Перед записью CSV целиком валидируется и дедуплицируется в памяти. Размер пакета
ограничивает нагрузку на PostgreSQL и Elasticsearch, но не потребление памяти
при разборе файла.

Исходный файл из задания: [posts.csv](https://disk.yandex.ru/d/UYooXd9q2yqTMQ).

## API

### Поиск

```http
GET /documents/search?q=<query>
```

Elasticsearch выбирает до 20 совпадений по полю `text` с анализатором
`russian`. Затем полные документы загружаются из PostgreSQL и сортируются по
`created_date` по убыванию, с `id` в качестве дополнительного ключа. Пустой
или состоящий из пробелов запрос возвращает `422`.

```bash
curl --get \
  --data-urlencode 'q=космическая программа' \
  http://127.0.0.1:8000/documents/search
```

### Удаление

```http
DELETE /documents/{document_id}
```

Документ сначала удаляется из PostgreSQL, затем из Elasticsearch. Ответ `204`
означает, что он существовал хотя бы в одном хранилище; `404` — что его не было
ни в одном. Ошибка хранилища возвращается как `503`, и запрос можно повторить.

## Архитектура

PostgreSQL — источник истины и хранит `id`, `rubrics`, `text` и `created_date`.
Elasticsearch содержит только `id` и `text`, необходимые для поиска. Индекс
создаётся при старте приложения и использует строгий mapping: `id` имеет тип
`keyword`, `text` — `text` с анализатором `russian`.

HTTP-слой отвечает за валидацию и коды ответа, сервисы — за порядок операций
между хранилищами, repository — за SQL. Repository не управляет commit или
rollback; транзакции открываются в сервисе импорта и в сервисе документов.

Bulk-индексация проверяет ошибки отдельных документов. Если Elasticsearch
содержит ID, которого уже нет в PostgreSQL, такой результат не возвращается и
рассинхронизация записывается в лог.

## Локальная разработка

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Основные настройки задаются переменными окружения с префиксом `APP_`:

- `APP_DATABASE_URL`;
- `APP_ELASTICSEARCH_URL` и `APP_ELASTICSEARCH_INDEX`;
- `APP_SEARCH_RESULT_LIMIT` (1–20, по умолчанию 20);
- `APP_IMPORT_BATCH_SIZE` (1–5000, по умолчанию 500);
- параметры Elasticsearch timeout/retry из `.env.example`.

Миграции:

```bash
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

Проверки проекта:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Для интеграционных тестов нужны отдельная PostgreSQL-база с суффиксом `_test`
и Elasticsearch-индекс с суффиксом `-test`:

```bash
docker compose up -d --wait postgres elasticsearch
export TEST_DATABASE_URL='postgresql+asyncpg://document_search:document_search_local@127.0.0.1:5432/document_search_test'
export TEST_ELASTICSEARCH_URL='http://127.0.0.1:9200'
export TEST_ELASTICSEARCH_INDEX='documents-test'
APP_DATABASE_URL="$TEST_DATABASE_URL" python -m alembic upgrade head
python -m pytest -m integration
```

## Ограничения

Проект не реализует аутентификацию, HTTP-создание и редактирование документов,
поиск по рубрикам, fuzzy-поиск, подсветку и пагинацию. `/health` проверяет только
работу процесса API, не доступность PostgreSQL и Elasticsearch.
