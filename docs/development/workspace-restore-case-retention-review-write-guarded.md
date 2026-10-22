# Обязательный credential для нового семейства write jobs

Статус: тридцать восьмой этап локального прототипа. Новое семейство заданий требует bearer credential при каждом поддержанном выполнении фазы и повторном получении сохранённого step-ответа. У него собственные intent, lock, отзыв и три неизменяемых слота результатов.

- [Исходная запись review](workspace-restore-case-retention-review-write.md).
- [Действие одной фазы](workspace-restore-case-retention-review-write-action.md).
- [Локальный bearer permit](workspace-restore-case-retention-review-write-permit.md).
- [Политика выдачи permits](workspace-restore-case-retention-review-write-issuer.md).
- [Полномочия принятия в Store](governance.md).
- [Дорожная карта](roadmap.md).

## 1. Какой пробел закрывается

Прежний native permit защищает выбранный execute. Для исходного write job сохраняются самостоятельные прямые write API. Поэтому добавление permit к прежнему заданию само по себе не делает предъявление credential обязательным для всех известных способов его продолжить.

Новый guarded job создаётся сразу в отдельном формате. Старые имена intent и lock в нём отсутствуют. Прежний публичный write reader и writer требуют свой lock; каталог также захватывает прежний job через этот lock. Поэтому известные старые маршруты отказывают до выполнения фазы нового задания. Продолжить его через новый публичный step можно только с подходящим credential и без сохранённого отзыва.

Граница относится к известным поддержанным review-write API. Произвольный код того же файлового владельца может читать ключи, вызывать частные helpers, делать системные вызовы и работать с независимыми копиями. Этот этап не создаёт ограничений операционной системы для такого кода.

## 2. Что защищается

| Объект | Значение |
| --- | --- |
| Guard | Неизменяемая привязка ключа и principal к одному новому write job |
| Вложенный write intent | Полная прежняя спецификация будущей review и её файловых bindings |
| Step result | Историческое наблюдение одного разрешённого перехода |
| Revocation | Необратимый отзыв допуска к последующим step-вызовам |
| Access | Текущее active/revoked собственных controls |
| Status | Отдельное текущее наблюдение физической фазы review |

Review остаётся совещательной исторической записью по retention plan. Работа с её частными файлами не разрешает архивирование, удаление исходных данных, принятие предложений в Store либо публикацию изменений документации.

Новый job не наследует lease и issuer policy прежнего семейства. Они не проверяются новым step; соответствующие публичные признаки равны false. Поддержанного преобразования старого job в новый, присоединения старого permit либо автоматического переноса политики нет.

## 3. Доверие к ключу, actor и principal

Подготовка доверяет оператору частной файловой среды. Он задаёт `principal`, самозаявленного `actor` review и credential. API принимает credential в памяти. CLI сохраняет сгенерированный секрет в частном файле и передаёт его без печати в ответе.

Владение bearer credential позволяет выполнять фазы одного guard до отзыва. SHA-256 предъявленного credential сравнивается с сохранённым хешем через `hmac.compare_digest`. Это сравнение с постоянным временем, а не вычисление HMAC credential. Глобального реестра ключей, проверки их уникальности между jobs и внешнего подтверждения личности principal нет.

Совпадение или различие actor, principal и credentials не доказывает разных людей, процессов либо независимых моделей. `credential_verified=true` относится к текущему допущенному вызову. `historical_effect_authenticated=false` сохраняет отсутствие удостоверенного авторства прежнего файлового эффекта.

В проверенной облачной среде процесс работает под UID/GID 1000; эффективные CAP_SETUID и CAP_SETGID отсутствуют. Системная изоляция между разными UID этим этапом не устанавливается. Публичный признак `os_isolation_established=false` отражает эту границу независимо от успешного выполнения протокола.

## 4. Отдельный формат каталога

Новый job содержит только разрешённый закрытый набор собственных имён:

~~~text
.agentwiki-restore-case-retention-review-write-guarded.json
.agentwiki-restore-case-retention-review-write-guarded.lock
.agentwiki-restore-case-retention-review-write-guarded.revoked.json
.agentwiki-restore-case-retention-review-write-guarded.prepared.result.json
.agentwiki-restore-case-retention-review-write-guarded.ready.result.json
.agentwiki-restore-case-retention-review-write-guarded.linked.result.json
.agentwiki-restore-case-retention-review-write.part
~~~

Последнее имя — прежний basename staging. Оно существует только до завершения. Отзыв и result slots необязательны до их сохранения. Старые `.agentwiki-restore-case-retention-review-write.json` и `.agentwiki-restore-case-retention-review-write.lock` никогда не создаются и не подставляются временно.

Каталоги имеют режим 0700. Управляющие JSON и lock имеют режим 0600, ожидаемого владельца, одну ссылку и не являются symlink. Постоянный lock остаётся пустым. Staging проверяется по прежним правилам фазы: в `linked` он разделяет inode с назначением и имеет две ссылки. Новые неизменяемые записи создаются эксклюзивно с fsync файла и каталога; произвольные соседние файлы в job не принимаются.

Fresh prepare отказывает для существующего старого job. Старый prepare также не усыновляет guarded job. Неполный bootstrap, частичный intent и несоответствующие controls не ремонтируются поддержанным повтором.

## 5. Неизменяемая привязка к review

Guard имеет identity `wrrwg_` с 32 шестнадцатеричными символами и rules version `workspace-restore-case-retention-review-write-guarded-1`. Его частная запись содержит ровно 11 полей:

~~~text
schema_version, rules_version, guard_id, operation_id, created_at,
job_path, job_directory, principal, credential_hash, write, request_digest
~~~

`write` содержит полное прежнее intent13, включая точную будущую review14, её историческое основание и файловые pins. Это вложенное тело проверяется прежним валидатором intent без чтения live plan/review и offline-реконструкцией известного plan14. Валидатор также проверяет metadata собственного job и родителя. Прежний intent-файл на диске для этого не требуется.

Внешние operation ID, время, путь и job-directory binding должны совпадать с вложенными. Canonical digest привязывает полное собственное тело; он не публикуется. Время подготовки конечно, положительно и не предшествует времени захваченного retention plan.

При новой подготовке используются новый job lock и прежний review lock. Захват и проверка будущей review сохраняют прежние правила. Старый публичный prepare не вызывается; исходный deadline вложенным helper не сбрасывается.

## 6. Три фазы и три слота результатов

Каждый step объявляет ожидаемую исходную фазу и отдельный operation ID:

| `expected_state` | Точная цель | Разрешённый собственный эффект |
| --- | --- | --- |
| `prepared` | `ready` | Заполнить и fsync собственный staging |
| `ready` | `linked` | Установить эксклюзивный hardlink review |
| `linked` | `completed` | Удалить доказанный собственный staging alias |

Один новый step выполняет не более одного прежнего файлового примитива. Для первого перехода допускается продолжение валидного собственного префикса в физическом состоянии `staging`. Это прежняя ограниченная возможность восстановления заполнения.

Для каждого expected state существует свой единственный result slot. Сохранённый слот нельзя заменить новым operation ID. Тот же ID в другом слоте также конфликтует. Идентичный запрос возвращает сохранённый ответ даже после переходов следующих фаз.

Слоты проверяются независимо: искусственного требования непрерывной последовательности квитанций нет. Пропуск квитанции сам по себе не доказывает отсутствие ранее выполненного эффекта. Новый запрос для старой фазы при состоянии позже её точной цели получает `phase_changed`.

## 7. Порядок допуска и блокировок

~~~mermaid
flowchart TD
    A[Новый job lock и собственный guard] --> B[Проверить credential и отсутствие отзыва]
    B --> C[Проверить все существующие result slots]
    C --> D{Точный сохранённый слот?}
    D -->|Да| E[Вернуть прежний Step22 без live plan и review]
    D -->|Нет| F[Review lock с исходным deadline]
    F --> G[Наблюдать текущую фазу новым reader]
    G --> H{Точное положение?}
    H -->|PRE или допустимый staging| I[Один прежний файловый примитив]
    H -->|POST| J[Наблюдение без собственного эффекта]
    H -->|Позже POST| K[phase_changed]
    I --> L[Result slot, fsync, проверка публичного ответа]
    J --> L
~~~

Ключ и отсутствие отзыва проверяются до чтения содержимого result slots и до live review/staging. Это правило действует и для saved replay. Наличие исторической квитанции не позволяет получить её через защищённый step без текущего допуска.

Новый запрос удерживает job → review locks до fsync результата, проверки публичного ответа и возврата. Отзыв использует тот же job lock. Последующий revoke ожидает уже допущенный step; начатый эффект не отменяется задним числом.

Все вложенные операции получают общий кооперативный deadline 60 секунд. Нового полного интервала при входе в helper нет. Такой deadline ограничивает поддержанные ожидания и проверки; он не обеспечивает принудительного отката системного вызова.

Новая оркестрация использует reader, понимающий новые имена controls. Старый общий action helper нельзя переносить целиком: его завершение обращается к reader старого семейства. Собственные файловые примитивы заполнения, синхронизации и проверки принадлежащих staging bytes сохраняют прежнюю реализацию.

## 8. Выполненная фаза и наблюдение готовой цели

Observation содержит ровно девять полей:

~~~text
rules_version, guard_id, operation_id, expected_state, target_state,
recorded_at, outcome, observed_state, effect_performed
~~~

После собственного примитива сохраняется `outcome=phase_performed` и `effect_performed=true`. При точном POST без собственного result допускается `outcome=expected_phase_observed` и `effect_performed=false`. В обоих случаях observed state должен точно совпадать с целью выбранного слота.

POST observation устанавливает наблюдаемое положение файлов, а не причину, автора или сохранность чужого предыдущего вызова. Переход эффекта и запись result находятся в разных файловых операциях. Общей атомарной транзакции между ними нет.

После эффекта и до durable result возможен промежуток без квитанции. Поддержанный reader способен распознать точный POST; новая observation честно фиксирует отсутствие собственного эффекта этого вызова. Частичный result не считается отсутствующим и не переписывается. Протокол не ремонтирует повреждённые управляющие записи.

Ветка POST без result и продолжение собственного staging описываются как возможности, подтверждаемые исходным кодом. Обычный успешный прогон не демонстрирует все прерывания, ошибки хранения или потерю питания. Для заявления о выполненной аварийной демонстрации потребовался бы отдельный явно описанный запуск.

## 9. Стабильный replay и текущее состояние

| Интерфейс | Источники | Что сообщает |
| --- | --- | --- |
| `show` | Собственный guard, slots и marker | Текущее active/revoked guard |
| `status` | Собственные controls и live review под locks | Текущую физическую фазу |
| Точный `step` replay | Собственные controls после нового допуска | Прежний ответ выбранной фазы |
| Точный `prepare` replay | Собственные controls и точный запрос | Прежний компактный Guard22 |

Step22 стабилен целиком: в него не добавляются свежий clock, live status и новый auth log. Историческое наблюдение `ready` остаётся таким же после завершения job. Текущее `completed` следует запрашивать отдельно через status.

Точный step replay не читает live plan или review. Поэтому он может вернуть прежний ответ при недоступных исходных путях, пока собственные controls целы, credential верен и отзыва нет. Ошибка или отзыв выявляются до чтения тела квитанции.

Точный prepare replay сверяет operation ID, principal, хеш credential, пути plan/review, actor и verdict. Он работает по собственным controls после завершения и отзыва и возвращает прежний Guard22. Подготовка не возобновляет отозванный guard и не создаёт нового эффекта.

`show` и `status` не требуют credential. Они раскрывают только предусмотренные публичные summaries; наблюдение состояния не является допуском к step. Прежний completed review reader также может прочитать историческую завершённую review; права записи нового семейства из этого не следуют.

## 10. Отзыв

Holder предъявляет тот же credential для отзыва своего guard. Сохраняется отдельный marker7 с полями schema version, rules version, guard ID, operation ID, временем и двумя привязочными digests. Публичное представление Revocation5 содержит rules version, guard ID, operation ID, revoked_at и `reason=holder_revoked`.

Отзыв необратим. Нет TTL, unrevocation, regrant или замены ключа в существующем guard. Для другого допуска создаётся другое новое задание с собственной identity и допустимым новым назначением review.

Точный revoke replay требует тот же credential и operation ID и возвращает прежний Access20. Другой ID конфликтует с уже сохранённым отзывом. Свежий revoked_at не предшествует guard и временам всех существующих slots; точный replay не требует нового clock.

После marker защищённый step блокируется и для новой фазы, и для сохранённого ответа. Уже выполненный эффект, прочитанные bytes и ранее возвращённые результаты не отзываются. Отзыв не меняет другие jobs, копии файлов или permits прежнего семейства.

## 11. Частные и публичные формы

| Форма | Полей | Содержание |
| --- | --- | --- |
| Частный guard | 11 | Полный nested intent13, principal, credential hash и digest |
| Частный result | 6 | Привязка к intent, Observation9 и собственный digest |
| Частный revoke | 7 | Отзыв и привязка к guard |
| Guard | 22 | Шесть основных полей и 16 признаков границы |
| Access | 20 | Четыре основных поля и те же признаки |
| Status | 21 | Access и прежний публичный write status17 |
| Step | 22 | Шесть основных полей и те же признаки |

Шесть основных полей Guard22 — rules version, guard ID, operation ID, created_at, principal и компактный write descriptor7. Descriptor содержит прежние rules version, write ID, operation ID, created_at, review ID, actor и verdict. Полное частное основание из него не восстанавливается.

Частный Result6 содержит `schema_version`, `rules_version`, `guard_id`, `intent_digest`, `observation`, `result_digest`. Intent digest равен digest guard, а digest самого result вычисляется без собственного поля. Фиксированный filename соответствует expected state. Новое recorded_at не предшествует guard и всем уже сохранённым slot clocks; exact replay проверяется раньше нового clock.

Step22 включает guard, observation и `credential_verified=true`, `revocation_checked=true`, `historical_effect_authenticated=false`. Access включает guard, access_state и необязательную публичную revocation. Status добавляет отдельно проверенный write status с теми же write/review identity, actor и verdict.

Общие 16 признаков во всех новых публичных формах:

~~~text
advisory_only=true                 inspection_only=true
domain_writes_allowed=false        original_files_modified=false
archival_authorized=false          deletion_authorized=false
actor_authenticated=false          old_authority_activated=false
external_fencing_established=false local_capability_only=true
external_identity_verified=false   legacy_write_apis_rejected=true
lease_enforced=false               issuer_policy_enforced=false
os_isolation_established=false     credential_required_for_step=true
~~~

Полные controls, hashes, digests, pins и credential не публикуются. Публичные формы проходят отдельные проверки точного состава и согласованности; совпадение произвольного JSON с внешним префиксом не является доказательством валидной записи.

## 12. API

Модуль `agentwiki.workspace_restore_case_retention_review_write_guarded` предоставляет:

~~~python
prepare_guarded_review_write(plan, review, job,
                             operation_id=operation_id,
                             actor=actor, verdict=verdict,
                             principal=principal, credential=credential)
inspect_guarded_review_write(job)
inspect_guarded_review_write_access(job)
step_guarded_review_write(job, operation_id=operation_id,
                          expected_state="prepared", credential=credential)
revoke_guarded_review_write(job, operation_id=operation_id,
                            credential=credential)
~~~

Аргументы actor, principal и operation ID используют ограниченные локальные идентификаторы. Verdict соответствует прежнему набору review. Plan, review и job должны удовлетворять прежним правилам отдельных частных путей и нового семейства.

## 13. CLI и хранение credential

Примеры предполагают существующий retention plan и частный родительский каталог `/tmp/agentwiki-private` с режимом 0700. Имена и operation IDs условные; команды не являются журналом выполненного запуска.

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-guarded prepare \
  --plan /tmp/agentwiki-private/plan-a \
  --review /tmp/agentwiki-private/review-a \
  --job /tmp/agentwiki-private/guarded-a \
  --operation-id guarded-a-01 --actor local-reviewer --verdict defer \
  --principal local-holder \
  --credential-out /tmp/agentwiki-private/guarded-a.credential

python3 -m agentwiki workspace-restore-case-retention-review-write-guarded show \
  --job /tmp/agentwiki-private/guarded-a
python3 -m agentwiki workspace-restore-case-retention-review-write-guarded status \
  --job /tmp/agentwiki-private/guarded-a
~~~

Для точного prepare replay передают `--prepared-credential` с прежним файлом вместо `--credential-out`. Новый credential-файл не перезаписывает существующий. Alias с job, review и plan отклоняется до генерации секрета.

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-guarded step \
  --job /tmp/agentwiki-private/guarded-a --operation-id guarded-fill-01 \
  --expected-state prepared \
  --credential-file /tmp/agentwiki-private/guarded-a.credential
python3 -m agentwiki workspace-restore-case-retention-review-write-guarded step \
  --job /tmp/agentwiki-private/guarded-a --operation-id guarded-link-01 \
  --expected-state ready \
  --credential-file /tmp/agentwiki-private/guarded-a.credential
python3 -m agentwiki workspace-restore-case-retention-review-write-guarded step \
  --job /tmp/agentwiki-private/guarded-a --operation-id guarded-complete-01 \
  --expected-state linked \
  --credential-file /tmp/agentwiki-private/guarded-a.credential
python3 -m agentwiki workspace-restore-case-retention-review-write-guarded revoke \
  --job /tmp/agentwiki-private/guarded-a --operation-id guarded-revoke-01 \
  --credential-file /tmp/agentwiki-private/guarded-a.credential
~~~

CLI принимает credential только через файл `.credential`: непосредственный сосед job в том же private parent, режим 0600, владелец текущего процесса, nlink=1, размер до 256 байт, отсутствие symlink и стабильные metadata при чтении. Секрет имеет формат `awg_` и 43 символа token; literal secret в argv не предусмотрен.

Сгенерированный файл и его родитель fsync выполняются до core prepare. Общей транзакции credential-файла с job controls нет. Неподдержанный bootstrap остаётся закрытым для продолжения; CLI не выводит секрет, его хеш или частное тело ошибки.

## 14. Пределы и ошибки

Частный guard intent и текущий status ограничены 16 МиБ. Остальные controls, results, revoke и компактные публичные ответы ограничены 64 КиБ. Для JSON сохраняются пределы 200000 узлов и глубины 64. Слотов результатов не более трёх; новые SQL-таблицы не вводятся.

Безопасный префикс ошибок — `workspace_restore_case_retention_review_write_guarded_`. Используются коды `invalid_input`, `private_path_required`, `limit_exceeded`, `conflict`, `incomplete`, `record_invalid`, `source_changed`, `credential_invalid`, `revoked`, `clock_regressed`, `phase_changed`, `io_failed`, `summary_invalid`. CLI сообщает ограниченную ошибку без публикации частных данных.

## 15. Что требуется показать в демонстрации

Демонстрация нового семейства должна отдельно фиксировать:

1. Подготовку нового guard и отказ прежних step/status/prepare/catalog маршрутов до эффекта; отказ нового prepare для старого job.
2. Отказ неверного credential, замены ID занятого слота и использования одного ID для разных фаз.
3. Три обычных перехода до completed и отсутствие нового staging после завершения.
4. Потерю целого публичного ответа первого step и его точное получение после следующих фаз; active credential при этом остаётся действующим.
5. Исторический replay по собственным controls при временно недоступных новых plan/review, отдельно от текущего status.
6. Отзыв, отказ новой и сохранённой step-операции после отзыва, сохранение доступности own show, exact prepare и exact revoke.
7. Неизменность исходных файлов, старых examples и остальных частных механизмов; отсутствие credential и частных controls в экспорте.

Для проверки недоступности источников следует создавать отдельный новый retention plan и новую review. Временное скрытие их собственных новых путей не должно менять исходные примеры. Восстановление путей выполняется в `finally`.

Рабочий driver получает публичные API/CLI ответы и разрешённые metadata, без чтения private controls, SQL и staging bodies. Отдельный read-only auditor может сверить guard11, nested intent13, plan14, review14, три result6 и revoke7 с публичными outputs. Результат такого аудита и выполненный запуск требуют отдельных фактических артефактов.

## 16. Значение для множества агентов

Семейство позволяет дать нескольким соблюдающим протокол процессам один определённый файловый маршрут: неизменяемая review, обязательный локальный ключ, сериализованный допуск и воспроизводимые ответы отдельных фаз. Три слота ограничивают историю одного задания и устраняют необходимость выдавать новое текущее состояние за прежнюю квитанцию.

Для одного job рассмотрим N callers с подходящим credential. Все они проходят через один job lock: фактические эффекты фаз сериализованы, и в один момент допущенную фазу выполняет один holder lock. Совпадающие запросы с тем же ID получают одну сохранённую квитанцию; разные ID для занятого слота конфликтуют. Три слота означают три разрешённых перехода одного задания. Они не задают число агентов и не измеряют пропускную способность.

Разные jobs могут перекрывать работу во времени, если их назначения, locks, файловые bindings и области допуска действительно разделены и хранилище это позволяет. Общие смысловые основания документов, ресурсы Store и редакционные решения требуют координации верхних слоёв. Наличие нескольких job locks само по себе не разрешает общий семантический конфликт.

Параметр `--workers 3` относится к унаследованному базовому сценарию. Новое guarded продолжение не является замером параллелизма. Расчётные 835 уникальных дочерних PID полного сценария относятся к последовательности запусков; это число нельзя трактовать как 835 одновременно работающих процессов.

Это основание для управляющего сервиса технической документации. Следующие самостоятельные задачи — ограничение доступа процессов к приватным файлам, внешняя identity, распределённое fencing, управляемая выдача ключей именно для нового семейства и редакционные правила семантического рассмотрения. Их совместимость с этим протоколом потребует отдельной реализации.

Количество процессов, которым доступен API, не устанавливает независимость их выводов и не измеряет ёмкость системы. Для сравнения смыслов документов по-прежнему нужны захваченные основания, явные обязательства, проверка stale и отдельные полномочия принятия. Новый credential gate управляет локальным файловым продолжением review; истинность текста и разрешение публикации определяются другими слоями.

## 17. Результаты обычного запуска

Исправленный обычный сценарий `normal02` завершился с exit code 0. Отчёт сохранил время `2026-10-22T20:38:36.472179+02:00`, часовой пояс Europe/Berlin и summary из 102 полей. Основания: [публичный отчёт](../../examples/workspace-restore-case-retention-review-write-guarded/results/restore-case-retention-review-write-guarded-report.md), [описание примера](../../examples/workspace-restore-case-retention-review-write-guarded/README.md) и [предшествующая часть переписки](../sessions/session-040/transcript.md).

| Показатель | Результат `normal02` |
| --- | --- |
| Публичные файлы | 467 в одном каталоге: 447 JSON и 20 Markdown |
| Новые публичные файлы | 18 |
| Новые ordinary CLI | 60: 36 успешных, 24 ожидаемых отказа |
| В том числе guarded CLI | 40: 23 успешных, 17 ожидаемых отказов |
| Полный сценарий | 821 CLI и 14 унаследованных interrupt workers |
| Уникальные дочерние PID | 835 за последовательность запусков |
| Новые guard, retention plan и review | По одному |
| Частные credential-файлы | Один новый, 33 всего |
| Сохранённые phase results | Три `phase_performed` |
| POST observations без собственной квитанции | Ноль |
| Финальное состояние нового job | `completed`, guard отозван |

Целый успешный ответ первого prepared-step действительно был отброшен. После завершения следующих фаз его точный ответ восстановлен при временно недоступных новых plan и review. Активный credential позволил получить исторические ответы всех трёх слотов без live источников. Затем отзыв заблокировал новый step и защищённую выдачу сохранённого ответа. Собственные show, exact prepare и exact revoke сохранили свои отдельные правила. Временно скрытые пути восстановлены.

Первый запуск `normal01` завершился ошибкой driver: свежая released lease page сравнивалась с прежней целиком, включая новый `checked_at`. Исправленное сравнение исключает только это поле времени; остальной состав страницы и освобождённая голова по-прежнему должны совпадать точно. Второй запуск выполнен в отдельной свежей среде. Первая среда сохранена без изменений, продукт и её данные не ремонтировались. Поэтому успешный результат относится к исправленному `normal02`; успех первого исполнения не заявляется.

Число 835 PID описывает выполненную последовательность дочерних запусков. Одновременное перекрытие процессов, пропускная способность guarded jobs, число независимых агентов и качество смыслового рассмотрения этим прогоном не измерялись. Новых fault workers, injected partial writes и ожиданий expiry не добавлялось. Ветка POST без квитанции остаётся возможностью, подтверждаемой исходным кодом, без выполненной аварийной демонстрации на этом этапе.

Все 14 завершающих read-only аудитов завершены успешно: новые guarded private/public; прежние historical/lease/action/permit/issuer private; прежние baseline/action/permit/issuer public; полный public schema; proof и privacy/preservation. При первом исполнении полного public helper обнаружена ошибка глобального счётчика plan_id: он должен различать пять retention wrrp и четыре reconciliation rcp. Исправлен только временный helper: проверяются точные namespace schemas, обе группы и полное объединение всех девяти IDs. Повтор helper прошёл; продукт и экспорт не менялись.

Новый focused public review выполнил128746 сравнений, включая18 побайтовых копий. Вместе с четырьмя прежними scoped public reviews —523847 logical/shape и467 byte comparisons, всего524314. Такие счётчики включают обычные проверки структуры JSON и не определяют число независимых экспертных оценок. Полный аудитор отдельно сверил102 summary-поля,6 полных stage payloads, все60 ordered CLI outcomes и835 уникальных PID.

Private audit подтвердил Guard11, nested intent13, новый plan14/review14, три Result6/Observation9, Revoke7, полные pins/digests, совпадение credential hash и отсутствие прежних intent/lock и финального staging. В новых публичных данных сопоставлены42 Guard22,12 Access20,10 Status21,11 Step22/Observation9 и10 revocation5. Privacy сохранил6083 прежних файла и43 frozen модуля, проверил526 известных credential и412 private hashes без их публикации. Proof связал3606 исторических ссылок. Корректность локального протокола и этих конечных данных сохраняет описанные выше границы доверия.
