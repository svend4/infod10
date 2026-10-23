# Происхождение двух выбранных значений urllib3 Retry

Время отчёта: 2026-10-23T13:45:32.645060+02:00 (Europe/Berlin). Отдельные captured reports используют UTC.

Сценарий читает исходник и Python-адаптер как данные; их код не импортируется и не исполняется. [Запись происхождения](source-import.json) связывает сохранённый upstream коммит и локальные байты корпуса.

Baseline: [upstream source](inputs/upstream-retry.py), [ручной адаптер](inputs/adapter.py), [авторский документ](inputs/contract.md), [явный профиль](inputs/profile.json), [лицензия MIT](inputs/LICENSE.txt).

## Восемь обычных случаев

| Случай | Match | Mismatch | Unresolved | Состояние |
|---|---:|---:|---:|---|
| [baseline](reports/baseline.json) | 2 | 0 | 0 | current_for_human_review |
| [adapter-total-11](reports/adapter-total-11.json) | 1 | 1 | 0 | blocked |
| [computed-total](reports/computed-total.json) | 1 | 0 | 1 | blocked |
| [shadowed-frozenset](reports/shadowed-frozenset.json) | 1 | 0 | 1 | blocked |
| [duplicate-class](reports/duplicate-class.json) | 0 | 0 | 2 | blocked |
| [ambiguous-fence](reports/ambiguous-fence.json) | 0 | 0 | 2 | blocked |
| [bool-total](reports/bool-total.json) | 1 | 0 | 1 | blocked |
| [mixed-unresolved-and-mismatch](reports/mixed-unresolved-and-mismatch.json) | 1 | 0 | 1 | blocked |

## Наблюдаемый результат

В исходном снимке извлечены шесть методов из Retry.DEFAULT_ALLOWED_METHODS и integer default total=10; два выбранных значения совпали с прежним ручным адаптером и JSON fence документа. Это синтаксическая проекция выбранных объявлений.
Отдельный авторский адаптер с TOTAL_DEFAULT=11 дал mismatch. Выражение 5 + 5 не вычислялось; связывание frozenset, повтор класса, повтор fence и True вместо integer оставили соответствующие endpoints unresolved. В смешанном случае неразрешимый source сохранил mismatch adapter/document.
Каждый analyze прошёл последующий show с тем же summary. Затем inputs временно отсутствовали целиком: исторический show пересчитал baseline по captured inputs собственного отчёта. Директория возвращена в finally. Попытка analyze в существующий baseline report отказала; его bytes и metadata не изменились.

## Публичные основания

[Случаи и ответы](case-observations.json), [18 CLI outcomes](cli-outcomes.json), [чтение без inputs](offline-observations.json), [отказ перезаписи](output-collision.json), [машинный отчёт](report.json).

## Границы

Всего 16 выбранных mapping результатов: 7 match, 1 mismatch, 8 unresolved. Попарных сравнений 48: 29 match, 3 mismatch, 16 unresolved. Endpoints 48: 40 разрешены, 8 не разрешены. Один случай current_for_human_review, семь blocked. Принятия либо публикации содержания нет.
Выполнены 18 ordinary CLI (17 успешных, один ожидаемый отказ), 18 различных PID без измерения перекрытия. Публичное дерево: 26 файлов — 15 JSON, 3 Markdown, 7 Python inputs и одна лицензия. Базы, credentials, worker pool и внешние LLM не создавались.
Грамматика не устанавливает runtime binding frozenset, поведение конструктора и сетевых веток, полноту документации, истинность прозы либо независимость проверяющих. Единица retries сохраняется как авторская аннотация; отсутствие POST в наборе не означает запрета всех его повторов. Семь производных случаев являются явными авторскими fixtures, а не изменениями upstream, эталонной разметкой или бенчмарком.
