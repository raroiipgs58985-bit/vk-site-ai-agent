ИСПРАВЛЕНИЕ НУЛЕВЫХ РЕЗУЛЬТАТОВ LEXICANUM

Причина: Lexicanum не разрешает API-доступ с IP бесплатного Render без белого списка.
Эта версия не обращается к Lexicanum напрямую с Render. Она использует Tavily Search,
ограниченный доменом wh40k.lexicanum.com, а OpenRouter формирует русский ответ.

Заменить в репозитории vk-site-ai-agent:
- app.py
- config.py
- service.py

Добавить:
- tavily_search.py

В Render АГЕНТА добавить:
SITE_SEARCH_MODE=tavily
TAVILY_API_KEY=tvly-...

Остальные ключи не менять:
SITE_BASE_URL=https://wh40k.lexicanum.com/
OPENROUTER_API_KEY=...
AGENT_SECRET=...

Переменную MEDIAWIKI_API_URL можно удалить: в режиме tavily она не используется.
