# Полный backup рабочей среды и цепочки истории

Время отчёта: 2026-10-21T22:54:11.481892+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий двух переносов](chain-report.md). Его публичные результаты
сохранены без перезаписи. Новый полный комплект объединяет quarantined снимок текущей базы
и оба предковых архива; частные origin, intent, manifest и планы демонстратором не читаются.

## Последовательность

| Этап | Наблюдение |
|---|---|
| [active-workspace-with-two-archived-hops](workspace-stage-01-active-workspace-with-two-archived-hops.json) | The active R4 workspace has two complete ancestral archives and a normally loadable release. |
| [current-authorization-and-source-boundaries](workspace-stage-02-current-authorization-and-source-boundaries.json) | Only the live imported workspace and its current administrator may authorize a new full backup. |
| [full-backup-captures-current-state-and-all-archives](workspace-stage-03-full-backup-captures-current-state-and-all-archives.json) | The completed full bundle contains quarantined R4 records and both unchanged fenced ancestral archives. |
| [live-authors-continue-after-capture](workspace-stage-04-live-authors-continue-after-capture.json) | Three live author jobs and one new composition use current source grants after the full backup. |
| [fresh-source-decisions-and-acceptance](workspace-stage-05-fresh-source-decisions-and-acceptance.json) | Four fresh decisions authorize revision 5 only in the live source; the full backup still describes R4. |
| [later-revocation-does-not-rewrite-captured-history](workspace-stage-06-later-revocation-does-not-rewrite-captured-history.json) | The live reviewer grant is now revoked; the quarantined R4 copy retains its earlier historical state. |
| [full-inspection-and-restore-with-all-source-paths-unavailable](workspace-stage-07-full-inspection-and-restore-with-all-source-paths-unavailable.json) | All three original workspace paths are temporarily renamed; the full bundle still inspects and restores complete quarantined history. |
| [restored-point-in-time-records-remain-quarantined](workspace-stage-08-restored-point-in-time-records-remain-quarantined.json) | The restored current DB retains R4 content, dependencies, tasks, events and receipts; live R5 and the later revocation remain separate. |
| [restored-authority-and-format-boundaries](workspace-stage-09-restored-authority-and-format-boundaries.json) | Current credentials cannot turn historical copied rows into writes, publication, backup authority or transfer authority. |
| [complete-history-remains-stable-after-live-continuation](workspace-stage-10-complete-history-remains-stable-after-live-continuation.json) | Repeated full inspection preserves the captured identity, both archives and quarantined restore while the original source stays active at R5. |

## Захват и последующая работа

Полный backup сохраняет ревизию 4 с её зависимостями, задачами, событиями и квитанцией.
Три новых авторских задания, композиция и четыре свежих решения затем принимаются в исходной
активной базе как ревизия 5. Используются её существующие действующие роли; копия не участвует
в новой записи. Последующий отзыв reviewer B также остаётся изменением живого источника.

## Самодостаточная история и восстановление

Три первоначальных частных каталога временно переименованы. Полный комплект успешно проверяется
и восстанавливается без этих путей; Store читает восстановленную ревизию 4 и оба архивных Store.
Имена прежних каталогов восстановлены в finally, публичные наблюдения совпали. Это обратимая
недоступность путей, без моделирования аварии процесса или дискового повреждения.

Восстановленная база остаётся в карантине. Прежние grants и решения читаются как история;
enqueue, submit, acceptance replay, publish, backup и transfer не получают полномочий записи.
Обычный DB-only inspector сохраняет свой отдельный формат и не принимает полный комплект.

## Публичные основания

- [Безопасные сводки](workspace-summaries.json), [CLI](workspace-cli-outcomes.json), [отказы](workspace-rejections.json).
- [Живой источник](workspace-source-observations.json), [backup](workspace-backup-observations.json), [restore](workspace-restored-observations.json).
- [Архивы](workspace-archive-observations.json), [цепочка квитанций](workspace-lineage.json), [прежний snapshot](workspace-old-snapshot-observations.json).
- [Зависимости](workspace-dependencies.json), [выпуски](workspace-releases.json), [недоступность путей](workspace-detached-path-observations.json).
- [Авторы](workspace-authors.json), [композиция](workspace-composition.json), [пакеты](workspace-packets.json).
- [Решения](workspace-decisions.json), [рассмотрение](workspace-observation.json), [принятие](workspace-acceptance.json), [отзыв](workspace-revocation.json).
- [Content-политика](workspace-content-policy-history.json), [governance](workspace-governance-policy-history.json), [машинный отчёт](workspace-report.json).

## Границы выполненного

Новых этапов: 10; новых CLI-процессов: 7; ожидаемых отказов: 15.
Захваченная и восстановленная ревизия: 4; поздний источник: 5.
События захвата: 36; позднего источника: 59; restore: 36.
Архивов: 2; событий: 64 и 38.
Авторских заданий: 3; наблюдавшихся новых потоков: 3; свежих решений: 4.
Программируемые роли не являются независимой человеческой экспертизой. Внешние LLM и
предлагаемый Python не исполнялись; измерений предельной ёмкости нет. Отдельный stale query/read
случай, частичный bundle, повреждение байтов и авария процесса не воспроизводились.

Публичная сверка исключает только вычисляемый верхний governance.observed_at. Полные
адресованные доказательства, raw timestamps и признаки expired сохранены. Частные базы,
контрольные суммы, manifests, планы и credentials не публикуются. Выпуски остаются в каталоге запуска.
