# Фиксированный каталог случаев и текущие наблюдения метаданных

Время отчёта: 2026-10-22T17:17:30.935938+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий исторических случаев](restore-cases-report.md); его 287 файлов сохранены.
Первый каталог получает три случая в явно заданном порядке. Второй явно включает более поздний четвёртый случай.

## Этапы

| Этап | Наблюдение |
|---|---|
| [fixed-membership-and-recovered-receipt](restore-case-catalog-stage-01-fixed-membership-and-recovered-receipt.json) | An explicit ordered three-case catalog preserves its receipt after a real discarded response and pages as two plus one entries. |
| [late-case-and-later-link](restore-case-catalog-stage-02-late-case-and-later-link.json) | A later case stays outside the original catalog; an explicit second catalog includes it and later observes its link to an already completed attempt. |
| [missing-case-preserves-membership](restore-case-catalog-stage-03-missing-case-preserves-membership.json) | A missing case yields a per-entry unavailable observation while pinned historical membership and exact create replay remain available. |
| [changed-case-preserves-membership](restore-case-catalog-stage-04-changed-case-preserves-membership.json) | An existing later case temporarily occupies another member path; the different directory is reported changed and both originals are restored. |
| [metadata-only-pages-and-separate-detail](restore-case-catalog-stage-05-metadata-only-pages-and-separate-detail.json) | Case-control pages remain available with linked attempt jobs and source absent; ordinary case show separately reports the unavailable current attempt. |
| [originals-and-domain-history-preserved](restore-case-catalog-stage-06-originals-and-domain-history-preserved.json) | Both catalog controls, three original cases and blocked jobs retain their observations; completed details and R4/R5 domain history remain unchanged. |

## История и текущие метаданные

Настоящий успешный create-ответ проверен и отброшен; точный replay восстановил ту же квитанцию.
Пагинация первого каталога возвращает 2 + 1 запись и пустую конечную страницу. Поздний случай автоматически не добавляется.
После явного создания второго каталога его поздний случай получает link с уже завершённой попыткой.
При недоступном либо заменённом пути исторический member остаётся; current_case отдельно сообщает unavailable или changed.

Доступность case controls не означает доступность связанной попытки. Страницы не читают старое evidence или jobs.
При временно скрытых attempt jobs и source bundle каталог читался, а обычный case show сообщил unavailable текущей попытки.
Все пути возвращены в finally. Старые блокирующие jobs не перемещались и сохранили прежние fingerprints.

## Публичные основания

- [Квитанции](restore-case-catalog-receipts.json), [страницы](restore-case-catalog-pages.json), [состав](restore-case-catalog-membership.json).
- [CLI](restore-case-catalog-cli-outcomes.json), [отказы](restore-case-catalog-rejections.json), [восстановление ответа](restore-case-catalog-reply-recovery.json).
- [Наблюдения controls](restore-case-catalog-control-observations.json), [подробные case show](restore-case-catalog-restored-detail-observations.json).
- [Исторический источник](restore-case-catalog-source-observations.json), [действующая среда](restore-case-catalog-live-observations.json), [машинный отчёт](restore-case-catalog-report.json).

## Границы результата

Этапов: 6; новых CLI: 44; ожидаемых отказов: 14; сохранённых страниц: 18.
Новых прерываний нет. Частичные записи каталога, аппаратные отказы, все расписания, удаление и предел нагрузки не воспроизводились.
Частные control records, staging и контрольные суммы демонстратор не разбирает. Публикуются безопасные API/Store ответы и булевы наблюдения.
Неизвестная identity старого plan остаётся неизвестной; операторская связь не доказывает равенство запросов.
Внешние LLM, независимая экспертиза, истинность содержания и внешний fencing не установлены.
