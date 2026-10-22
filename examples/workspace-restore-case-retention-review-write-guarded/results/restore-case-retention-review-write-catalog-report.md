# Явный каталог заданий записи и отдельные текущие наблюдения

Время отчёта: 2026-10-22T20:37:13.118947+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий поэтапной записи](restore-case-retention-review-write-report.md); его 359 публичных файлов сохранены.
Первый каталог закрепляет long и minimum. Позднее подготовленное задание включено только во второй явно созданный каталог.

## Этапы

| Этап | Наблюдение |
|---|---|
| [explicit-membership-freezes-order](restore-case-retention-review-write-catalog-stage-01-explicit-membership-freezes-order.json) | A catalog explicitly pins long then minimum; its actual discarded create response is recovered by exact replay, and bounded pages preserve order and terminal emptiness. |
| [later-job-needs-an-explicit-new-catalog](restore-case-retention-review-write-catalog-stage-02-later-job-needs-an-explicit-new-catalog.json) | A later prepared job is not automatically added to the first catalog. The second pins long, late and minimum from intent metadata even while the late target is unavailable; pages retain that row and recover after the target returns. |
| [pages-observe-phases-without-advancing](restore-case-retention-review-write-catalog-stage-03-pages-observe-phases-without-advancing.json) | Only ordinary write steps advance the later job through ready, linked and completed. Catalog pages retain the same descriptor and show each current status without changing controls; compatible review and separate revalidation remain advisory. |
| [unavailable-and-changed-members-stay-visible](restore-case-retention-review-write-catalog-stage-04-unavailable-and-changed-members-stay-visible.json) | An absent job retains one unavailable row. Swapping two existing completed job directories retains two changed rows before intent parsing; restoring paths restores ordinary observations and every historical descriptor remains fixed. |
| [historical-catalog-survives-input-loss](restore-case-retention-review-write-catalog-stage-05-historical-catalog-survives-input-loss.json) | With twenty explicit paths absent, both shows and exact create replay remain historical and pages retain unavailable member rows. After the six job/target paths return, pages observe all jobs while fourteen original source paths are still absent; every path returns in finally. |
| [catalogs-do-not-authorize-domain-actions](restore-case-retention-review-write-catalog-stage-06-catalogs-do-not-authorize-domain-actions.json) | Invalid cursors and different ordered requests refuse safely. Final catalogs, completed writes, previous reviews, case catalogs, case details and blocked jobs preserve their histories; no catalog page advances a job or grants authority. |

## Исторический состав и текущая фаза

Каталоги сохраняют порядок и компактные descriptor7. Страницы возвращают их рядом с текущими status17 либо явными changed/unavailable; строки не исчезают из-за ошибки участника.
Второй каталог создан при временно отсутствующем review target позднего job. Сохранение membership читает intent metadata, а подробный текущий status наблюдается отдельно.
Обычные step позднего задания дали ready, linked и completed; страницы только наблюдали эти фазы, не меняя управляющие файлы.
Проверены страницы с cursor, терминальная пустая страница, недоступный job и два других job directories на закреплённых путях. Возврат путей восстановил наблюдения без изменения membership.
При отсутствии 20 путей show и exact create replay сохранились, а страницы явно вернули unavailable. После возврата шести job/target paths все задания снова доступны, хотя 14 прежних source inputs ещё отсутствуют.
Все пути возвращены в finally. Исходные blocked jobs никогда не перемещались. Отброшенный настоящий create reply восстановлен точным повтором; сравнительный digest не опубликован.

## Публичные основания

- [Каталоги](restore-case-retention-review-write-catalog-receipts.json), [состав](restore-case-retention-review-write-catalog-membership.json), [20 страниц](restore-case-retention-review-write-catalog-pages.json).
- [CLI](restore-case-retention-review-write-catalog-cli-outcomes.json), [отказы](restore-case-retention-review-write-catalog-rejections.json), [восстановление ответа](restore-case-retention-review-write-catalog-reply-recovery.json).
- [Controls](restore-case-retention-review-write-catalog-control-observations.json), [задания и review](restore-case-retention-review-write-catalog-write-observations.json).
- [Источник](restore-case-retention-review-write-catalog-source-observations.json), [живая среда](restore-case-retention-review-write-catalog-live-observations.json), [машинный отчёт](restore-case-retention-review-write-catalog-report.json).

## Границы результата

Шесть этапов, 66 новых обычных CLI: 47 успешных и 19 ожидаемых отказов. С прежним основанием это 437 CLI и 14 унаследованных interrupt workers, всего 451 различных дочерних процессов.
Созданы два каталога с пятью выбранными members и одно позднее задание записи с одним новым review. Всего сохранены три write jobs и пять review records.
Source/живая история сохранили R4/36 событий и R5/59. Прежние каталоги, reviews, case details, данные и blocked jobs сохранены.
Демонстратор не разбирает private control bodies или staging contents. Публикуются безопасные API/Store ответы, счётчики и булевы metadata-наблюдения.
Новых fault workers, SIGKILL, injected partial writes и отказов хранения нет. Предельный параллелизм, throughput и смысловое качество документации не измерялись.
Исторический состав, available status и completed не удостоверяют участника и не разрешают доменных записей, архивирования, удаления либо активации прежних полномочий.
