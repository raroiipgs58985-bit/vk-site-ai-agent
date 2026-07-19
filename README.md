# VK Site AI Agent — Lexicanum / MediaWiki edition

Отдельный HTTP-сервис для «Кубятни». При команде `[найди ...` он:

1. получает русский вопрос;
2. через Qwen создаёт английские поисковые формулировки и термины Warhammer 40,000;
3. ищет подходящие статьи через MediaWiki Search на английском Lexicanum;
4. скачивает только найденные статьи вместо случайного обхода десятков тысяч страниц;
5. анализирует текст и возвращает русский ответ со ссылками.

Если MediaWiki API отвечает ошибкой или блокирует запрос, агент автоматически пробует обычную HTML-страницу `Special:Search`.

## Настройки для Lexicanum на Render

Обязательные секреты:

```text
GROQ_API_KEY=gsk_...
AGENT_SECRET=длинная-случайная-строка
```

Настройки сайта:

```text
SITE_BASE_URL=https://wh40k.lexicanum.com/
SITE_SEARCH_MODE=mediawiki
MEDIAWIKI_API_URL=https://wh40k.lexicanum.com/mediawiki/api.php
MEDIAWIKI_ARTICLE_PATH=/wiki/{title}
```

Ограничения поиска:

```text
SITE_MAX_PAGES=40
SITE_DEEP_MAX_PAGES=100
MEDIAWIKI_RESULTS_PER_QUERY=8
MEDIAWIKI_DEEP_RESULTS_PER_QUERY=15
MEDIAWIKI_QUERY_LIMIT=6
MEDIAWIKI_DEEP_QUERY_LIMIT=8
SITE_CONCURRENCY=3
SITE_REQUEST_DELAY=0.5
RESPECT_ROBOTS_TXT=true
```

Обычный поиск обычно читает до 40 уникальных статей, глубокий — до 100. На практике из-за пересечения результатов часто будет прочитано меньше.

## Render

При ручном создании Web Service:

```text
Build Command:
pip install -r requirements.txt

Start Command:
gunicorn app:app --workers 1 --threads 4 --timeout 600 --bind 0.0.0.0:$PORT

Health Check Path:
/health
```

Либо создайте Blueprint по файлу `render.yaml`.

## API

### `GET /health`

Проверяет конфигурацию.

### `POST /ask`

```json
{
  "question": "Кто отвечает за сбор планетарной Десятины?",
  "deep": false
}
```

Требуется заголовок:

```text
Authorization: Bearer AGENT_SECRET
```

Ответ содержит русский текст, ссылки, число прочитанных статей, время работы и поле `search_backend: "mediawiki"`.
