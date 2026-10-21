# Совещательные планы хранения и сохранённые причины удержания

Время отчёта: 2026-10-21T23:58:36.781866+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий каталога](restore-case-catalog-report.md); его 305 файлов сохранены.
Четыре плана сохраняют выбранный диапазон, политику и фиксированное время оценки. Операций архивации и удаления нет.

## Этапы

| Этап | Наблюдение |
|---|---|
| [captured-policy-and-recovered-response](restore-case-retention-stage-01-captured-policy-and-recovered-response.json) | A zero-retention advisory snapshot holds unknown original identities and identifies two known completed cases for review; exact replay recovers a real discarded response. |
| [minimum-age-holds-all-members](restore-case-retention-stage-02-minimum-age-holds-all-members.json) | A separately saved long retention policy holds every selected case while preserving unknown-identity reasons and fixed evaluation time. |
| [missing-case-remains-held-in-history](restore-case-retention-stage-03-missing-case-remains-held-in-history.json) | A missing known case becomes a held row with its pinned historical receipt; restoring the path does not rewrite that saved observation. |
| [unavailable-attempt-is-a-separate-blocker](restore-case-retention-stage-04-unavailable-attempt-is-a-separate-blocker.json) | Available case controls retain unchanged original evidence while an unavailable linked attempt holds that row; the ordinary case detail agrees. |
| [saved-snapshots-outlive-live-path-availability](restore-case-retention-stage-05-saved-snapshots-outlive-live-path-availability.json) | Show for all four plans and exact replay remain historical with catalogs, cases, linked job directories and source unavailable; every path is restored. |
| [originals-and-domain-history-preserved](restore-case-retention-stage-06-originals-and-domain-history-preserved.json) | Advisory planning leaves case/catalog/job controls and domain history unchanged; original blocked jobs still refuse and saved plans retain fixed decisions. |

## Политика и исторические наблюдения

При retain_seconds=0 два известных завершённых случая получают review_candidate; две неизвестные исходные identity остаются held.
Отдельный длинный срок удерживает все четыре случая. Временное отсутствие известного case либо его linked attempt даёт отдельную причину удержания.
Возврат исходных путей не переписывает сохранённые решения. Show и точный replay сохраняют тот же исторический snapshot и время оценки.
Настоящий успешный ответ plan проверен и отброшен; точный повтор восстановил ту же квитанцию со всеми строками.

При временно скрытых catalogs, cases, attempt job directories и source bundle все четыре show и точный replay оставались доступны.
Все пути возвращены в finally. Старые blocked jobs не перемещались; текущие catalog pages, case details и R4/R5 история сохранились.
Review candidate требует отдельного рассмотрения. archival_authorized=false и deletion_authorized=false сохраняются во всех планах.

## Публичные основания

- [Планы](restore-case-retention-plans.json), [политики и причины](restore-case-retention-policy-observations.json).
- [CLI](restore-case-retention-cli-outcomes.json), [отказы](restore-case-retention-rejections.json), [восстановление ответа](restore-case-retention-reply-recovery.json).
- [Историческое чтение без live paths](restore-case-retention-offline-observations.json), [сохранность controls](restore-case-retention-control-observations.json).
- [Подробные case show](restore-case-retention-restored-detail-observations.json), [источник](restore-case-retention-source-observations.json), [живая среда](restore-case-retention-live-observations.json), [машинный отчёт](restore-case-retention-report.json).

## Границы результата

Этапов: 6; новых CLI: 38; ожидаемых отказов: 15; сохранённых планов: 4.
Новых прерываний нет. Частичные записи, аппаратные отказы, все расписания, удаление и предел нагрузки не воспроизводились.
Частные control records и staging демонстратор не разбирает. Публикуются безопасные API/Store ответы и булевы наблюдения.
Неизвестная исходная identity не реконструируется; план не предоставляет полномочий архивации или удаления.
Внешние LLM, независимая экспертиза, истинность содержания и внешний fencing не установлены.
