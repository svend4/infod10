# Объявленные обещания и проверяемое происхождение выбранных значений

Время отчёта: 2026-10-23T17:10:42.446627+02:00 (Europe/Berlin). Полные coverage reports используют UTC.

Новая явная опись связывается с точными ID/digest полного source-adapter report. [Запись происхождения](source-import.json) фиксирует три неизменённых родительских отчёта и отдельные авторские inventories.

Родительские inputs: [baseline](inputs/source-baseline.json), [adapter total=11](inputs/source-adapter-total-11.json), [mixed unresolved](inputs/source-mixed-unresolved-and-mismatch.json), [baseline inventory](inputs/inventory-baseline.json), [лицензия MIT](inputs/LICENSE.txt).

## Десять обычных случаев

| Случай | Covered / total | Matching | Unresolved | State |
|---|---:|---:|---:|---|
| [baseline](reports/baseline.json) | 2 / 2 | 2 | 0 | current_for_human_review |
| [uncovered-total](reports/uncovered-total.json) | 1 / 2 | 1 | 0 | blocked |
| [invalid-requirements](reports/invalid-requirements.json) | 0 / 2 | 0 | 0 | blocked |
| [missing-quote](reports/missing-quote.json) | 1 / 2 | 1 | 0 | blocked |
| [ambiguous-quote](reports/ambiguous-quote.json) | 1 / 2 | 1 | 0 | blocked |
| [fenced-quote](reports/fenced-quote.json) | 1 / 2 | 1 | 0 | blocked |
| [shared-duplicates](reports/shared-duplicates.json) | 3 / 3 | 3 | 0 | current_for_human_review |
| [empty](reports/empty.json) | 0 / 0 | 0 | 0 | blocked |
| [omitted-parent-mismatch](reports/omitted-parent-mismatch.json) | 1 / 1 | 1 | 0 | blocked |
| [mixed-parent-unresolved](reports/mixed-parent-unresolved.json) | 2 / 2 | 1 | 1 | blocked |

## Наблюдаемый результат

Два исходных обещания имеют точные уникальные цитаты вне fenced blocks и правильные полные метаданные mapping. Удалённая ссылка, неизвестный ID, изменённая scope, отсутствующая и неоднозначная цитаты не создают покрытия. Уникальный фрагмент JSON fence сохраняет snippet/location, но anchor остаётся unresolved.
Повтор требования даёт 3/3 covered promises и 2/2 unique requirements; shared mapping и duplicate requirement показаны отдельно. Пустая опись блокируется с null fractions. При исключении проблемного total из описи healthy methods дают 1/1 coverage, но parent mismatch и полные upstream comparisons сохраняют blocked. Mixed source unresolved сохраняет известный adapter_document mismatch.
Каждый analyze прошёл show с тем же summary. Затем inputs отсутствовали целиком: historical show пересчитал baseline по собственному полному отчёту, directory возвращена в finally. Analyze в существующий output отказал; bytes/metadata исходного report не изменились.

## Публичные основания

[Случаи и ответы](case-observations.json), [22 CLI outcomes](cli-outcomes.json), [чтение без inputs](offline-observations.json), [отказ перезаписи](output-collision.json), [машинный отчёт](report.json).

## Границы

В десяти авторских иллюстрациях 18 promise rows: 12 covered, из них 11 matching и одна unresolved. Это повторное использование выбранных обещаний и связей; сумма по случаям не является долей полноты документации. Два случая current_for_human_review, восемь blocked. В 20 upstream mapping comparisons сохранены 18 match, один mismatch и один unresolved; все 60 pair outcomes видимы: 55 match, 3 mismatch, 2 unresolved.
Выполнены 22 ordinary CLI: 21 successful и один ожидаемый отказ, 22 различных PID без измерения concurrency. Публичное дерево: 31 файл — 29 JSON, один Markdown и одна лицензия. Базы, credentials, worker pool, принятие и внешние LLM не создавались.
Outside_fenced_blocks относится только к ограниченной грамматике Markdown fences; HTML, inline и indented code и смысл прозы не распознаются. Точная цитата и совпадение всех селекторов не доказывают соответствие смысла текста полю, полноту описи, runtime behavior или независимость участников. Полные captured sources и inventories являются историческими данными; current freshness и atomic capture не установлены. Новые inventories составлены явно, прежние coverage-1 файлы не преобразовывались. Human review требуется; auto_accept=false.
