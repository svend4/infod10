# Поэтапная запись рассмотрения

Тридцать второй этап продолжает [рассмотрение сохранённых планов](../workspace-restore-case-retention-review/README.md). Отдельный private job сохраняет точную будущую review и принадлежность staging; дальнейшие шаги завершают запись без исходных plan/catalog/case/job paths.

## Запуск

Из корня существующего checkout укажите новый каталог:

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-demo \
  --workspace /tmp/agentwiki-retention-review-write-01 --workers 3
~~~

Используются Python и стандартная библиотека. Внешние модели и серверы не требуются.

## Фактический результат

Обычный сценарий завершился с первой попытки 21 октября 2026 года в 23:58:59 Europe/Berlin. Сохранены 359 публичных файлов: 345 JSON и 14 Markdown. Прежнее основание даёт 341 файл; новые шесть этапов, десять JSON-артефактов и два отчёта добавляют 18.

Подготовлены два write jobs, сохраняющие точные review для планов minimum и long. Они завершены тремя фазами `ready` → `linked` → `completed`. Изначальные review ID, время, actor/verdict, планы и все причины удержания сохранены.

| Наблюдение | Выполненный результат |
| --- | --- |
| Два step callers запущены до сбора ответов | Получены ready и linked в неопределённом порядке |
| Прежний reader до публикации или при двух hard links | Явный отказ; result остаётся null |
| Третий step | Совместимая review с единственной ссылкой |
| Все 14 прежних live paths отсутствуют | Подготовленное задание завершается без них |
| Отброшенный ответ prepare | Восстановлен точным повтором prepare |
| Отброшенный ответ ready-step | Восстановлен read-only status |

Выполнены 58 новых обычных CLI: 41 успешный и 17 ожидаемых отказов. С прежним основанием это 371 CLI и 14 унаследованных interrupt workers, всего 385 различных процессов. Фактическое перекрытие времени двух callers, производительность и предел нагрузки не измерялись.

После возврата путей прежние review, plans, catalogs, cases и blocked jobs сохранились; исходные blocked jobs не перемещались. Обе новые review доступны прежним show/record replay. Отдельные revalidations сохраняют minimum с двумя held и long с четырьмя held при совпавшем наблюдаемом основании. Source/живая история остаётся R4/36 событий и R5/59. Новых grants, credentials, restore attempts, доменных решений, архивирования и удаления сверх основания нет.

Независимые read-only сверки подтвердили точные intent/review bindings, inode конечных файлов, single links, отсутствие staging, права 0700/0600 и сохранность данных. Публичный аудит проверил строгие схемы и 337 ссылок отчётов; другой обзор выполнил 71034 логических сравнения и 359 побайтных сравнений экспортов. Privacy-сверка сохранила 3617 прежних защищённых файлов и не обнаружила известных credentials, их хешей и частных привязок в 4124 кандидатах публикации.

## Материалы

- [Контракт и анализ](../../docs/development/workspace-restore-case-retention-review-write.md).
- [Читаемый отчёт](results/restore-case-retention-review-write-report.md).
- [Машинный отчёт](results/restore-case-retention-review-write-report.json).
- [Предыдущее основание](../workspace-restore-case-retention-review/results/restore-case-retention-review-report.md).

Обычные этапы отделены от моделирования отказов. Прерывания процесса, частичная запись intent и предел нагрузки этим сценарием не исследуются. Actor самозаявленный; обзор остаётся совещательным. Частные intent, pins, paths, контрольные суммы, SQLite и credentials остаются вне Git.
