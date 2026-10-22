# Явный каталог заданий записи review

Тридцать третий этап продолжает [поэтапную запись рассмотрения](../workspace-restore-case-retention-review-write/README.md). Отдельный частный каталог фиксирует выбранные jobs и их порядок. Страница сохраняет каждого участника рядом с текущим status либо явной ошибкой привязки/доступности.

## Запуск

Из корня существующего checkout укажите новый каталог:

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-catalog-demo \
  --workspace /tmp/agentwiki-review-write-catalog-01 --workers 3
~~~

Используются Python и стандартная библиотека; внешние модели и серверы не требуются.

## Фактический результат

Обычный сценарий завершился с первой попытки 22 октября 2026 года в 15:48:02 Europe/Berlin. Сохранены 377 публичных файлов: 362 JSON и 15 Markdown. Прежнее основание даёт 359 файлов; шесть этапов, десять JSON-артефактов и два отчёта добавляют 18.

Первый каталог закрепил порядок `[long, minimum]`. Позднее подготовлен новый job; прежний список остался из двух участников. Второй каталог явно закрепил `[long, late, minimum]`, включая late из immutable intent при временно недоступной цели review. Дальнейшие страницы сохранили его descriptor и показали prepared, ready, linked и completed после отдельных обычных write steps.

| Наблюдение | Результат |
| --- | --- |
| Недоступна цель review либо сам job | Исторический участник остаётся с unavailable |
| На закреплённых путях другие job directories | Сохраняются changed-строки до разбора replacement intent |
| Недоступны 20 исходных и member/target путей | Show и exact replay доступны; page сохраняет unavailable-строки |
| Шесть member/target путей возвращены, 14 исходных ещё отсутствуют | Полные текущие status снова доступны без исходных данных |

Выполнены 66 новых обычных CLI: 47 успешных и 19 ожидаемых отказов. Из них 46 относятся к каталогу: 30 успешных и 16 отказавших. С прежним основанием это 437 CLI и 14 унаследованных interrupt workers, всего 451 различный процесс. Сохранены два каталога, пять membership entries, три различных write jobs и 20 именованных страниц.

Настоящий ответ create отброшен и восстановлен точным replay. Все пути возвращены в finally; исходные blocked restore jobs никогда не перемещались. Page не меняла управляющие файлы и не выполняла step. Исходная и живая история сохранили R4/36 событий и R5/59; новых credentials, grants, restore attempts, архивирования и удаления сверх повторённого основания нет.

Независимые read-only сверки подтвердили частные canonical records/pins, descriptor/status, все страницы и 355 ссылок отчётов. Отдельный обзор выполнил 144562 логических сравнения и 377 побайтных сравнений копий. Privacy-сверка сохранила 3983 прежних защищённых файла и не обнаружила известных credentials, их хешей и частных привязок в 4512 кандидатах публикации. Публичный schema-аудит прошёл после исправления конфликта имени в его временном скрипте; данные и реализация при этом не менялись.

## Материалы

- [Контракт и анализ](../../docs/development/workspace-restore-case-retention-review-write-catalog.md).
- [Читаемый отчёт](results/restore-case-retention-review-write-catalog-report.md).
- [Машинный отчёт](results/restore-case-retention-review-write-catalog-report.json).
- [Прежнее основание](../workspace-restore-case-retention-review-write/results/restore-case-retention-review-write-report.md).

Каталог не обнаруживает jobs автоматически, не выполняет step и не выдаёт leases. Actor самозаявленный, решения совещательные. Частные controls, paths, pins, SQLite, credentials и контрольные суммы остаются вне Git.
