# Выполненный постоянный запрет записи исходной среды

Время отчёта: 2026-10-22T21:54:02.171469+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала выполнен [сценарий сверки восстановления](recovery-report.md). Его публичные основания
сохранены без перезаписи. Источник продолжил работу, а прежняя копия осталась в карантине.

## Последовательность

| Этап | Наблюдение | Событий источника |
|---|---|---:|
| [active-source-and-historical-copy](fence-stage-01-active-source-and-historical-copy.json) | The source continues at its current state; the old restored copy remains quarantined. | 62 |
| [stale-and-unauthorized-seals-rejected](fence-stage-02-stale-and-unauthorized-seals-rejected.json) | An old plan and a revoked non-administrator leave the source unfenced and unchanged. | 62 |
| [publication-and-seal-launched-together](fence-stage-03-publication-and-seal-launched-together.json) | Two ordinary requests share a launch barrier. Publication either completes under its writer reservation or observes the committed fence. | 63 |
| [persistent-fence-remains-readable](fence-stage-04-persistent-fence-remains-readable.json) | An existing Store and a reopened Store read the current history and observe the same irreversible source fence. | 63 |
| [exact-replay-and-input-conflicts](fence-stage-05-exact-replay-and-input-conflicts.json) | The original operation returns the saved receipt without another event; a different operation or plan conflicts. | 63 |
| [normal-source-writes-and-replays-blocked](fence-stage-06-normal-source-writes-and-replays-blocked.json) | Existing and reopened Store paths reject ordinary writes, old leases, historical write replays and publication before their action bodies. | 63 |
| [live-authority-helpers-reject-fenced-source](fence-stage-07-live-authority-helpers-reject-fenced-source.json) | A fenced source cannot authorize new backup or recovery assessment through the live-source APIs. | 63 |
| [historical-copy-retains-its-quarantine](fence-stage-08-historical-copy-retains-its-quarantine.json) | The historical copy remains unchanged and quarantined; the source remains readable with exactly one seal event. | 63 |

## Что зафиксировано

Свежий частный план и действующий исходный администратор закрепили постоянную метку в meta.
Запись метки и безопасного события выполнена одной транзакцией. Прежний план и отозванный
reviewer были отклонены до остановки. Содержание и задачи сохранены, добавлено одно событие.

После повторного открытия источник доступен для чтения. Точный повтор операции вернул исходную
квитанцию без нового события; другой operation ID или другой план вызвали конфликт.

## Публикация и остановка

Два обычных запроса — публикация и CLI seal — запущены через общий барьер в двух потоках.
Фактический исход публикации: `completed`. Новый publish после остановки отклонён до создания каталога.
Времена возврата запросов сохранены как наблюдения, без выдачи их за точное время commit.
Перекрытие попыток SQLite-блокировок не измерялось; обе очередности намеренно не навязывались.
Граница порядка опирается также на статически проверенную блокировку записи, охватывающую
рендеринг, запись файлов и уборку публикации. Успевший выпуск остаётся в каталоге запуска и
не входит в плоский набор файлов этого отчёта.

## Публичные основания

- [Квитанции и replay](fence-receipts.json), [статусы и доступ](fence-statuses.json).
- [Исходы CLI](fence-cli-outcomes.json), [конкурирующие запросы](fence-race.json).
- [Ожидаемые отказы](fence-rejections.json), [наблюдения источника](fence-observations.json).
- [События](fence-events.json), [неизменившаяся копия](fence-snapshot-unchanged.json).
- [Наблюдение replay](fence-read-observations.json), [машинный отчёт](fence-report.json).

## Граница результата

Новых этапов: 8; новых CLI-процессов: 4; ожидаемых отказов: 18.
Событий источника: 62 → 63; ревизия: 2; событий копии: 52.
Это запрет кооперативного текущего Store API для одной канонической базы. Старые версии Store,
сырой SQL и доверенный владелец SQLite способны его обойти. Внешний или распределённый fence
не установлен. Снятие запрета и активация старой копии через API отсутствуют.

Чтения старых backup/assessment могут завершить исторические или совещательные файлы позднее;
запрет всей файловой активности не обещается. Полное переключение рабочей среды требует
отдельного согласования внешних эффектов, новых полномочий и аренд.

Демонстратор не читает содержимое частных планов или envelope метки. Credentials, SQLite,
контрольные суммы и частные request digests не публикуются. Предлагаемый код не исполняется;
внешние LLM, имитация аварий и измерение предельной нагрузки отсутствуют.
