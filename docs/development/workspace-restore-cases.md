# Сохранённый случай блокировки и отдельная попытка восстановления

Статус: двадцать восьмой этап локального прототипа. Команды `workspace-restore-case` сохраняют частную историческую запись трёх ограниченных ранних отказов и позволяют явно связать её с уже существующим другим restore job.

**Обычный development-запуск успешно завершён 21 октября 2026 года.** Три case records связаны с отдельно созданными попытками. Исторические квитанции сохранились после их завершения, три отброшенных ответа восстановлены точным replay, а старые jobs остались блокирующими. Независимые публичная, private и privacy-сверки завершены успешно. Основания и границы приведены в разделе 13.

- [Пошаговое восстановление](workspace-restore-job.md).
- [Прерывания файлового шага](workspace-restore-interrupt.md).
- [Продолжение подготовки и три блокирующих остатка](workspace-restore-prepare.md).
- [Прикладной сценарий](../../examples/workspace-restore-cases/README.md).
- [Отчёт сценария](../../examples/workspace-restore-cases/results/restore-cases-report.md).
- [Машинный отчёт](../../examples/workspace-restore-cases/results/restore-cases-report.json).

## 1. От отказа к сохраняемому наблюдению

Предыдущий этап оставляет `plan_partial`, `intent_durable` и `owner_partial` блокирующими. Обычные prepare, status, step и resume-prepare не должны исправлять непроверяемые control records либо усыновлять назначение.

Следующее действие может быть полезным даже при сохранении такого отказа: зафиксировать доступное наблюдение, сохранить границу неизвестного и отдельно создать новую попытку обычным prepare. При этом старый остаток остаётся свидетельством прерывания.

Новая case record не является ремонтом старого задания. Она фиксирует историческое наблюдение и возвращает ограниченную безопасную квитанцию. Отдельная link record сохраняет явную операторскую ассоциацию с уже созданным другим заданием.

## 2. Что известно об исходном задании

Поддержаны только три ранних класса:

| Класс case | Доступное основание | Проверенная исходная identity |
| --- | --- | --- |
| `plan_invalid` | Ограниченный непроверяемый plan | `null`; полные job ID, operation ID, время, workspace ID и назначение из префикса не выводятся |
| `ownership_unproven` | Корректный plan и первое snapshot intent; точного owner нет | Identity из plan известна; принадлежность target не доказана |
| `owner_invalid` | Корректный plan, первый intent и malformed/noncanonical owner | Identity из plan известна; непроверяемый owner не становится доказательством принадлежности |

У `plan_invalid` знание caller о том, какой запрос он хотел выполнить, не заменяет сохранённую проверенную identity. Label демонстратора либо параметры прежнего worker также не восстанавливают её задним числом.

У двух других классов корректный plan сообщает намерение операции. Он сам по себе не доказывает, что существующий target действительно создан ею. Поэтому фиксация случая не смягчает исходный отказ.

Case API не является универсальным диагностическим интерфейсом для любых повреждений Store, произвольных каталогов или старых одношаговых restores.

`cause` называет поддержанную наблюдаемую форму, а не доказанный механизм её появления. Например, `plan_invalid` сам по себе не доказывает SIGKILL. Канонический owner с неверным содержимым не входит в ограниченный класс `owner_invalid` данного этапа.

Capture допускает только ранний состав job из plan/lock и, при необходимости, owner. У проверяемого plan назначение должно содержать ровно первый стандартный snapshot intent. Staging, основная DB, архивы и остальные metadata не включаются в эти три формы. Для `plan_invalid` предполагаемый target не извлекается из неполного plan и не осматривается.

## 3. Четыре разных результата

| Объект | Что он означает |
| --- | --- |
| Историческая case receipt | Что удалось зафиксировать об исходном ограниченном отказе |
| Текущее original evidence | Совпадает ли доступное наблюдение исходных файлов с сохранённым |
| Историческая link receipt | Какую уже существовавшую попытку оператор явно связал с case |
| Текущий attempt status | Что сейчас показывает обычная проверка связанного job |

Сохранённая квитанция не переписывается из-за последующего изменения файлов. Текущая недоступность исходного каталога не превращает старую запись в новое наблюдение.

Равенство наблюдений также не устраняет неизвестное. Для `plan_invalid` состояние `unchanged` означает сохранность зафиксированного остатка, а не появление полной identity запроса.

Флаг `original_files_modified=false` описывает действие record/link. Он не утверждает, что исходные файлы никогда не изменятся; show может отдельно сообщить `changed`.

## 4. Частные записи и граница публикации

Case record находится в отдельном частном каталоге. Она не добавляет неизвестных entries в старый job или готовый bundle. Отдельная append-once link record не является изменяемой головой продвижения.

| Частная запись | Ограничение |
| --- | --- |
| `.agentwiki-restore-case.json` | Неизменяемая case record, не более 32 KiB |
| `.agentwiki-restore-case.lock` | Постоянный пустой файл локальной блокировки |
| `.agentwiki-restore-case-attempt.json` | Неизменяемая link record, не более 64 KiB; появляется только при ассоциации |

Каталог имеет точный состав из двух либо трёх записей. Каталоги требуют 0700, файлы — 0600. Неизвестное содержимое не считается расширением формата.

Частное основание может связывать пути, filesystem identity и закреплённые наблюдения. Эти значения нужны для последующей сверки; публичные ответы не раскрывают private paths, checksums, request digests, manifests, SQL-отпечатки и содержимое неполного plan.

Пути case и участвующих jobs сохраняют предусмотренное расположение соседей с общим частным родителем. Старый job, новый job и case остаются разными объектами. Это локальная граница доступа владельца файлов, без внешнего удостоверения личности оператора.

Создание case не записывает, не удаляет и не исправляет исходные blocked files. Новое задание также создаётся вне case API обычным prepare с явно выбранными параметрами.

Каждая публичная операция ограничена сроком 60 секунд, включая ожидание блокировок. Для show/link удерживается case flock, а старое и новое задания наблюдаются последовательно под отдельными job flocks. Две job-блокировки одновременно не удерживаются.

При link сначала проверяется старое наблюдение, затем отдельно считывается новая попытка, после чего старое наблюдение проверяется повторно перед append link. Record выполняет предварительное чтение старого job, освобождает его lock до создания case и затем сверяет повторное чтение. Общего атомарного снимка двух jobs либо общей SQL-транзакции здесь нет.

## 5. Команды

Программный интерфейс модуля `agentwiki.workspace_restore_cases`:

~~~python
record_blocked_case(job_directory, case_directory, operation_id=operation_id)
inspect_case(case_directory)
link_attempt(case_directory, job_directory, operation_id=operation_id)
~~~

Сначала фиксируется ограниченный отказ:

~~~sh
python3 -m agentwiki workspace-restore-case record \
  --job /tmp/agentwiki-private/blocked-job \
  --case /tmp/agentwiki-private/restore-case \
  --operation-id record-blocked-01
~~~

Для наблюдения сохранённого случая и текущих файлов:

~~~sh
python3 -m agentwiki workspace-restore-case show \
  --case /tmp/agentwiki-private/restore-case
~~~

Другая попытка создаётся отдельно, обычной CLI:

~~~sh
python3 -m agentwiki workspace-restore prepare \
  --bundle /tmp/agentwiki-private/explicit-full-backup \
  --destination /tmp/agentwiki-private/new-restore \
  --job /tmp/agentwiki-private/new-restore-job \
  --operation-id new-restore-01
~~~

Затем явно сохраняется ассоциация с уже существующим заданием:

~~~sh
python3 -m agentwiki workspace-restore-case link-attempt \
  --case /tmp/agentwiki-private/restore-case \
  --job /tmp/agentwiki-private/new-restore-job \
  --operation-id link-attempt-01
~~~

В case API нет автоматического prepare, скрытого выбора source/target, глобального registry, list либо wrapper, создающего новую попытку. Обычные четыре restore-команды и их 15-полевой status сохраняются.

## 6. Публичная case receipt: 13 полей

| Поле | Значение |
| --- | --- |
| `rules_version` | `workspace-restore-case-1` |
| `case_id` | Локальный идентификатор `wrc_…` |
| `operation_id` | ID запроса фиксации случая |
| `created_at` | Фиксированное время исторической записи |
| `cause` | `plan_invalid`, `ownership_unproven` либо `owner_invalid` |
| `original_error_code` | `workspace_restore_job_manifest_invalid` для первого/третьего класса, `workspace_restore_job_conflict` для второго |
| `original_identity` | `null` у `plan_invalid`; иначе точные `job_id`, `operation_id`, `created_at`, `workspace_id` |
| `inspection_only` | `true` |
| `domain_writes_allowed` | `false` |
| `original_files_modified` | `false` |
| `original_identity_reconstructed` | `false` |
| `old_authority_activated` | `false` |
| `external_fencing_established` | `false` |

`original_error_code` использует прежний префикс `workspace_restore_job_`. Отсутствующая identity сохраняется как `null`, без подстановки безопасно выглядящих придуманных значений.

Публичный `operation_id` относится к record-запросу. Его нельзя принять за восстановленный ID старого запроса, если `original_identity` неизвестна.

## 7. Публичная link receipt: 14 полей

| Поле | Значение |
| --- | --- |
| `rules_version` | `workspace-restore-case-1` |
| `case_id` | Случай, к которому относится ассоциация |
| `link_id` | Локальный идентификатор `wrl_…` |
| `operation_id` | ID запроса сохранения ассоциации |
| `created_at` | Фиксированное время link record |
| `attempt_status` | Исторический 15-полевой status уже существовавшего job |
| `operator_association` | `true` |
| `fresh_creation_verified` | `false` |
| `request_equivalence_verified` | `false` |
| `original_files_modified` | `false` |
| `inspection_only` | `true` |
| `domain_writes_allowed` | `false` |
| `old_authority_activated` | `false` |
| `external_fencing_established` | `false` |

Связь не доказывает, что case API создал попытку, что она создана после записи случая либо повторяет тот же исходный запрос. Source и новые параметры явно выбирает caller обычного prepare.

Одна case допускает одну неизменяемую link record. Точный повтор её запроса возвращает исторический ответ; другой job или другой operation ID конфликтует. Это не глобальная уникальность job среди всех возможных case directories.

## 8. Show отделяет историю от текущего состояния

Ответ show имеет пять полей:

| Поле | Значение |
| --- | --- |
| `rules_version` | `workspace-restore-case-1` |
| `case` | Историческая 13-полевая case receipt |
| `link` | Историческая 14-полевая link receipt либо `null` |
| `original_evidence` | Отдельное текущее наблюдение с `state` и `error_code` |
| `current_attempt` | Отдельное текущее наблюдение с `state`, `status` и `error_code` |

Для исходных evidence предусмотрены:

| State | `error_code` |
| --- | --- |
| `unchanged` | `null` |
| `changed` | `workspace_restore_case_evidence_changed` |
| `unavailable` | `workspace_restore_case_evidence_unavailable` |

Текущее наблюдение не изменяет case receipt и не разрешает ремонт исходных файлов. Для `plan_invalid` назначение не извлекается из префикса и не наблюдается как якобы проверенный target.

Для попытки предусмотрены:

| State | `status` | `error_code` |
| --- | --- | --- |
| `not_linked` | `null` | `null` |
| `available` | Обычный 15-полевой status | `null` |
| `changed` | `null` | `workspace_restore_case_attempt_changed` |
| `unavailable` | `null` | `workspace_restore_case_attempt_unavailable` |

Статические pins связывают plan, owner, lock и идентичность каталогов job/target. Обычное продвижение `prepared` → `completed`, новые data files и их entries/timestamps не считаются заменой identity: та же попытка остаётся `available`. Невозможность текущей проверки либо несовпадение pins сообщаются отдельно, без публикации private exception.

Исторический `attempt_status` в link receipt остаётся прежним после обычного step. Продвижение того же job должно читаться из `current_attempt`, без переписывания link record.

Case API не пишет SQL и не открывает источник восстановления. Обычная проверка уже завершённого нового job может читать его данные и выполнять SQL inspect в режиме чтения; это не разрешение менять историческую DB.

## 9. Повторы и потеря ответа

У record-запроса, link-запроса и самого нового restore job отдельные operation IDs. Они фиксируют разные действия.

Точный record replay возвращает сохранённую историческую case receipt. Точный link replay возвращает прежнюю link receipt с её исходным `attempt_status`. Повтор не добавляет новое время и не выдаёт текущую проверку за прежний ответ.

Потерянный ответ ассоциации можно восстанавливать её точным повтором. Для текущей диагностики используется show. Эти два результата отвечают на разные вопросы, поэтому изменившийся текущий status не делает исторический replay новым restore.

Частичная запись case или link остаётся блокирующей. Этот этап не демонстрирует SIGKILL внутри создания новых case/link records и не обещает автоматически достроить их неполные метаданные.

## 10. Наблюдение не предоставляет полномочия

Зафиксированный отказ не является разрешением очистить каталог, удалить owner, дописать plan либо перенести прежние grants/leases. Операторская link record также не даёт права принимать документы, запускать предложенный код или активировать историческую DB.

Создание новой попытки требует отдельного явного выбора source, job, target и operation ID. Сам факт наличия case не подставляет эти параметры и не подтверждает эквивалентность запросов.

Для нескольких callers это позволяет разделить работу: один фиксирует доступное наблюдение, другой явно выбирает отдельную попытку, последующий процесс читает её продвижение. Число callers не определяет число логических результатов и не удостоверяет независимость участников.

Операционная связь между case и attempt относится к локальному файловому протоколу. Она не устанавливает смысловую истинность документов, distributed consensus, внешнюю авторизацию или предельную ёмкость по числу агентов.

## 11. Выполненный development-сценарий

Основанием послужил заново выполненный предыдущий сценарий подготовки с тремя сохранёнными blocked jobs. Для каждого из них выполнены:

1. Запись отдельного private case, точный record replay и show.
2. Отдельный обычный prepare с явно выбранным полным source и новыми job/target/operation ID.
3. Link уже существующей попытки, намеренное отбрасывание полученного успешного ответа и его точный replay.
4. Show с текущим status отдельно от исторической link receipt; конфликт конкурирующего job либо operation ID.
5. Обычный step до полного R4/36 комплекта с двумя архивами, inspect и историческое чтение Store.
6. Проверка карантина и повтор исходных отказов с сохранением трёх старых остатков.

Новые case APIs не создавали дополнительные аварии и не исправляли исходные control records вручную. Составной демонстратор сохранил повторённое основание из 269 публичных файлов и добавил материалы этого этапа.

## 12. Воспроизведение

Из корня существующего checkout укажите новый workspace:

~~~sh
python3 -m agentwiki workspace-restore-case-demo \
  --workspace /tmp/agentwiki-restore-cases-01 --workers 3
~~~

Нужны Python 3.10+, стандартная библиотека и предусмотренные POSIX private permissions. При обычном продолжении данных остаются прежние требования flock, hard links и проверки полного bundle.

## 13. Фактический запуск

Обычная команда успешно завершилась с первого запуска:

~~~sh
python3 -m agentwiki workspace-restore-case-demo \
  --workspace /tmp/infod10-workspace-restore-cases-development-01 --workers 3
~~~

Время завершения: `2026-10-21T18:56:13.092251+02:00`, Europe/Berlin. Публичный экспорт находится в `examples/workspace-restore-cases/results`; частные records и рабочие данные остались в каталоге запуска вне Git.

| Case cause | Историческая исходная identity | Status в link receipt | Текущая попытка после step | Текущее старое evidence |
| --- | --- | --- | --- | --- |
| `plan_invalid` | `null` | `prepared` | `available/completed` | `unchanged` |
| `ownership_unproven` | Четыре поля из проверенного plan | `prepared` | `available/completed` | `unchanged` |
| `owner_invalid` | Четыре поля из проверенного plan | `prepared` | `available/completed` | `unchanged` |

Квитанции сохранили прежние 13/14 полей и фиксированные timestamps. Show отдельно сообщил продвижение `prepared` → `completed`, не переписав исторический `attempt_status`. У всех трёх links сохранились `operator_association=true`, `fresh_creation_verified=false` и `request_equivalence_verified=false`.

| Наблюдение | Результат запуска |
| --- | --- |
| Публичные файлы | 287: 277 JSON и 10 Markdown |
| Новые материалы | 18: 5 этапов, 11 файлов данных и 2 отчёта; baseline — 269 файлов |
| Новые ordinary CLI | 50: 39 успешных ответов и 11 ожидаемых отказов |
| Составной сценарий | 186 обычных CLI и 14 workers повторённого основания, всего 200 child-процессов |
| Новые прерывания | Новых workers или SIGKILL данного этапа нет |
| Отброшенные ответы | Три фактически полученных и проверенных link-ответа отброшены; точный replay восстановил те же публичные квитанции |
| Выбор новых попыток | Три обычных prepare с явно переданным R4 source, новыми job/target/operation ID; case API их не создавал |
| Конкурирующие links | Шесть отказов: другой operation ID и другой job для каждого случая |
| Ограничения operation ID | Два ожидаемых отказа по длине: record и link-attempt |
| Старые jobs | Три прежних отказа status; наблюдённые metadata неизменны, усыновления и ремонта не было |
| Новые исторические результаты | Три полных R4/36 комплекта, каждый с двумя архивами; inspect и публичные Store-наблюдения совпали с источником |
| Действующая среда | R5, 59 событий; её публичные записи сохранились |
| Карантин | Шесть отказов enqueue/publish; старые grants/leases не активированы |
| Все отказы этапа | 17: 6 конкурирующих links, 2 length bounds, 3 старых blocked status и 6 карантинных операций |
| Чтение без исходного bundle | Финальные show/status, полный inspect и Store трёх новых результатов работали при временно недоступном source; путь возвращён в finally |

Основание восстановления ответа — [reply recovery](../../examples/workspace-restore-cases/results/restore-cases-reply-recovery.json). Успешный публичный ответ был получен и проверен перед отбрасыванием. Сравнительные digests не публиковались.

Публичные материалы:

- [Читаемый отчёт](../../examples/workspace-restore-cases/results/restore-cases-report.md) и [машинный отчёт](../../examples/workspace-restore-cases/results/restore-cases-report.json).
- [Три случая и receipts](../../examples/workspace-restore-cases/results/restore-cases-cases.json), [обычные CLI](../../examples/workspace-restore-cases/results/restore-cases-cli-outcomes.json) и [отказы](../../examples/workspace-restore-cases/results/restore-cases-rejections.json).
- [Старые jobs](../../examples/workspace-restore-cases/results/restore-cases-original-observations.json), [новые назначения](../../examples/workspace-restore-cases/results/restore-cases-target-observations.json), [архивы](../../examples/workspace-restore-cases/results/restore-cases-archive-observations.json) и [полные сводки](../../examples/workspace-restore-cases/results/restore-cases-completed-summaries.json).
- [Исходная сводка](../../examples/workspace-restore-cases/results/restore-cases-source-summary.json) и [действующая база](../../examples/workspace-restore-cases/results/restore-cases-live-observations.json).

После запуска выполнены отдельные независимые сверки:

- Публичная проверка подтвердила состав 287 файлов, строгие схемы case/link/show и обычных status/summary, 200 различных PID составного сценария, 50 новых CLI и 17 ожидаемых отказов. Ссылки внутри экспортированных результатов разрешаются.
- Privacy/structure-сверка root подтвердила сохранность 2333 прежних материалов и всех 287 экспортированных копий, корректное чтение JSON/Python и отсутствие проверенных значений credentials, fingerprints и control records в публичных кандидатах. Проверка охватывала полные и частичные restore plans, три case и три link records.
- Отдельная частная сверка root обнаружила 19 файлов плана: 18 корректных и один неполный, 16 завершённых комплектов с 48 файлами данных и три блокирующих задания. Три case и три link records прошли проверку канонической формы, request digests, неизменяемых pins, публичных привязок и сохранности полного старого evidence. Подтверждённая принадлежность новых назначений не переносилась на старые непроверенные targets.
- Для завершённых комплектов проверены закреплённые bytes исходной DB/архивов и результатов, постоянный карантин и полный origin, одна ссылка у файлов, отсутствие staging, тип/UID, права 0700/0600 и SQLite integrity/foreign keys. Исторические результаты сохраняют R4/36; действующая среда остаётся R5/59 с поздним reviewer-отзывом. Исторический `prepared` в links отдельно сопоставлен с текущим `completed` той же попытки.
- Частичный owner совпал с ожидаемым префиксом канонической записи. Неполный plan остался ограниченным непроверяемым объектом с неизвестной полной identity; она не реконструировалась. Сверки не исправляли старые файлы и не раскрывали частные значения.
- Полные адресованные пакеты и связи предшествующих этапов прошли отдельную сверку. Это проверка сохранности доказательств и ссылок; согласованность файлов не подтверждает смысловую истинность содержания.

Частные пути, планы, owner records, request digests, fingerprints и SQL-отпечатки не публикуются. Успешные сверки не превращают неизвестную исходную identity в известную и не расширяют три поддержанные формы до общего ремонта повреждений.

## 14. Границы результата

Поддерживаются три ограниченных ранних класса. Case не является общей очередью инцидентов, глобальным registry, распределённой блокировкой или универсальным средством ремонта.

Частная фиксация не доказывает внешнее авторство, полноту всех возможных событий либо невозможность последующей подмены владельцем файлов. Успешная новая попытка также не меняет исторический исход старой.

Окно mkdir назначения до первого snapshot intent, частичные case/link records, изменённые/недоступные старые jobs, произвольные повреждения, аппаратные отказы и измерение нагрузки не воспроизводились данным сценарием. Существующие примеры и прежние форматы restore job/bundle сохраняются.
