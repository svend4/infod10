# Авторский адаптер: выбранные значения Retry 2.5.0

Этот документ составлен авторами проекта по закреплённым исходникам urllib3;
это не документ, опубликованный командой urllib3.

Набор Retry.DEFAULT_ALLOWED_METHODS содержит HEAD, GET, PUT, DELETE, OPTIONS и TRACE; POST в нём отсутствует.

У конструктора Retry значение total по умолчанию равно 10; параметр считает повторы, а не все попытки.

```json agentwiki:retry-defaults
{
  "default_allowed_methods": ["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE"],
  "total_default": 10
}
```

Область: конструктор Retry и его значения по умолчанию в urllib3 2.5.0.
Это не утверждение о настройках любого PoolManager или приложения.
Набор методов не означает универсального запрета повторять POST: фильтр
методов применяется не ко всем категориям ошибок одинаково.
При total=10 исходная попытка не входит в счётчик повторов; разрешённые
повторы зависят от категории ошибки и остальных условий. Это не гарантия
11 обращений и не универсальный предел сетевых операций приложения.

<!-- Synthetic authored fixture: duplicate selected fence. -->
```json agentwiki:retry-defaults
{
  "default_allowed_methods": ["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE"],
  "total_default": 10
}
```
