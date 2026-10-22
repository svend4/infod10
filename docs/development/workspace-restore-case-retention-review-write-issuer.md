# Локальная политика выдачи допусков к действиям записи

Статус: тридцать седьмой этап локального прототипа. Отдельный issuer задаёт неизменяемую политику выдачи новых native permits. Квитанция issuance связывает одну такую выдачу с точным правилом, исходным issuer и конкретным permit.

- [Локальный bearer permit](workspace-restore-case-retention-review-write-permit.md).
- [Действие одной фазы](workspace-restore-case-retention-review-write-action.md).
- [Аренда позиции каталога](workspace-restore-case-retention-review-write-leases.md).
- [Исходная запись review](workspace-restore-case-retention-review-write.md).
- [Полномочия принятия в Store](governance.md).
- [Дорожная карта](roadmap.md).

## 1. Какой пробел закрывается

Предыдущий permit проверяет владение локальным bearer credential при защищённом выполнении и выдаче сохранённого результата. Его непосредственная выдача доверяет файловому владельцу. При нескольких исполнителях требуется также явно описать, какие новые permits может выдавать выбранный управляющий интерфейс.

Issuer закрепляет администратора, его отдельный credential и конечный перечень разрешённых сочетаний principal, worker, ledger, позиции и фаз. Новый управляемый issue проверяет соответствие этому перечню до резервирования каталогов выдачи и permit. Сохранённая issuance-квитанция позволяет прочитать точную историческую выдачу независимо от текущей доступности исходного action.

Это дополнительный интерфейс локальной политики. Прежняя прямая выдача permit и прямые action/write API сохраняют свою область. Обязательная изоляция всех writers потребует отдельного ограничения доступа к этим путям и файлам.

## 2. Разные основания работы

| Основание | Что фиксируется |
| --- | --- |
| Каталог write jobs | Явный состав и порядок заданий |
| Lease | Исходная локальная попытка назначения позиции |
| Action | Точный write intent, claim и одна пара PRE/POST |
| Issuer policy | Разрешённые сочетания параметров новой выдачи |
| Issuance | Историческая квитанция одной управляемой выдачи |
| Native permit | Отдельный bearer-допуск к точному action |
| Review | Историческое мнение по сохранённому предметному основанию |

Issuer не выполняет файловую фазу review и не продлевает lease. Новый native permit исполняется и отзывается через прежний permit-интерфейс. Выдача не заменяет проверку текущей аренды при новом эффекте и не предоставляет полномочий принятия предметного изменения.

## 3. Доверие к администратору и исполнителю

Создание issuer доверяет владельцу частной файловой среды. Оператор задаёт локальную метку `administrator` и сохраняет отдельный административный bearer credential. При управляемом issue предъявление этого секрета проверяется до доступа к дочерним управляющим записям; другой credential не получает прав выдачи только благодаря совпадению имени.

У будущего holder native permit собственный credential. Новый управляемый issue требует, чтобы его хеш отличался от административного. Это различие локальных ключей, а не доказательство разных людей, процессов или независимых моделей. Один оператор может владеть обоими ключами и несколькими principal.

Worker исходной lease и actor review остаются самозаявленными. Политика сравнивает точные строки и захваченные идентификаторы; она не удостоверяет человека, организацию, автора прежнего эффекта либо качество рассмотрения документа.

Владелец файлов и процесса остаётся доверенным оператором. Возможность напрямую читать credentials, менять controls, вызывать старые API или использовать независимую копию находится за пределами этой политики. Внешний identity provider, SSO, системная изоляция и внешнее fencing здесь не реализуются.

## 4. Неизменяемая политика

Политика — упорядоченный список от 1 до 16 правил. Каждое правило имеет ровно семь полей:

~~~json
{
  "rule_id": "fill-worker-a",
  "principal": "local-holder-a",
  "worker": "local-worker-a",
  "ledger_id": "wrrwlgr_0123456789abcdef0123456789abcdef",
  "index": 1,
  "before_state": "prepared",
  "target_state": "ready"
}
~~~

`rule_id`, administrator, principal и worker используют ограниченные локальные идентификаторы. Ledger имеет прежний формат `wrrwlgr_` с 32 шестнадцатеричными символами, index — целое от 1 до 256. Target должен точно соответствовать исходной фазе action:

| Before | Target |
| --- | --- |
| prepared | ready |
| staging | ready |
| ready | linked |
| linked | completed |

Уникальны и `rule_id`, и полный scope без `rule_id`. Wildcards, приоритетов и выбора первого подходящего пересекающегося правила нет. Порядок списка сохраняется как часть точного запроса создания.

При issue principal берётся из явного запроса. Worker, ledger ID и index сравниваются с сохранённым `action.lease`; пара фаз — с самим action. Совпасть должны все поля одного правила. Наличие разрешения для готового POST не разрешает произвольный переход через несколько фаз.

Политика не ограничивает номер поколения lease отдельным полем. Конкретный native permit по-прежнему закрепляет точный action и его исходный claim, включая поколение. Новое поколение не подменяет claim уже выданного permit.

Выдача может относиться к pending action либо к action с уже сохранённым результатом. Она не устанавливает текущую действительность старой lease или незавершённость job. Новый файловый эффект остаётся под прежней проверкой исходной аренды; выдача старого результата следует отдельным правилам native permit.

Политика issuer не редактируется на месте. Для другого перечня создаётся новый issuer с собственной identity. Общего реестра всех issuers, глобальной уникальности credentials и operation ID, перечня всех выдач либо счётчика выданных permits нет.

## 5. Новый issue и точная привязка

Все четыре каталога — issuer, issuance, native permit и action — должны быть разными непосредственными соседями в одном частном родительском каталоге. Известные action bindings ledger/catalog/member job также проверяются на alias до резервирования; для этого не требуется дополнительный обход живого каталога.

Под issuer lock проверяются собственные controls, административный credential и хеш holder credential. При новом запросе сохранённый отзыв issuer блокирует выдачу до доступа к action/native permit. Дальше захватывается action и выбирается точное правило политики.

Новый issue требует отсутствующего native permit. Существующий matching grant нельзя молча принять за созданный этой операцией. Собственная завершённая issuance-квитанция является отдельным основанием точного replay.

После preflight резервируются issuance и native permit. Action захватывается повторно, его bindings сравниваются с первоначальными. Native grant сохраняется по прежнему формату, с отдельным holder credential и исходным action. Затем записывается immutable issuance-квитанция с полными частными pins issuer и native grant.

Время native permit не может предшествовать времени его action либо issuer. Время issuance не может предшествовать native permit. Эти проверки связывают записи данной выдачи; глобальной упорядоченной истории часов всех выдач они не создают.

## 6. Блокировки и общий предел времени

Основной порядок — issuer → issuance → native permit → action. Предварительный action lock освобождается до создания issuance. После резервирования action захватывается уже под native permit lock; внешний issuer lock остаётся на протяжении всей допущенной выдачи.

~~~mermaid
flowchart TD
    A[Issuer lock, own grant и marker] --> B[Проверка admin credential и другого holder credential]
    B --> C{Есть complete issuance?}
    C -->|Да| D[Точный own receipt replay]
    C -->|Нет| E[Нет отзыва issuer]
    E --> F[Action preflight и точное правило]
    F --> G[Reserve issuance и native permit]
    G --> H[Recapture action и сравнение bindings]
    H --> I[Native grant, fsync]
    I --> J[Issuance receipt, fsync]
    J --> K[Историческая публичная квитанция]
    D --> K
~~~

Native permit lock удерживается до сохранения issuance-квитанции и формирования проверенного публичного ответа. Соблюдающий протокол отзыв issuer ожидает завершения уже допущенной выдачи. Он не отменяет начатую запись и не отзывает уже возвращённые bytes.

Все операции получают один кооперативный deadline 60 секунд. Вложенный helper не начинает новый полный интервал. Это предел ожиданий и проверок поддержанного кода, а не принудительный откат произвольного системного вызова.

Каждая управляющая запись и публичный ответ ограничены 64 КиБ. Для вложенного JSON сохраняются пределы 200000 узлов и глубины 64. Новых таблиц SQLite, CAS головы либо общего лимита числа issuance directories этот слой не добавляет.

## 7. Промежуток между двумя долговечными записями

Native grant и issuance receipt находятся в разных каталогах. Сначала fsync сохраняет native grant, затем — отдельную квитанцию issuer. Общей транзакции двух каталогов нет.

После сбоя в этом промежутке native permit может оказаться доступен через прежний API, хотя финальная issuance-квитанция не сохранена. Во время обычной выдачи соблюдающий протокол caller ожидает удерживаемый native permit lock до сохранения receipt. Сбой может оставить issuance с одним lock, неполный native permit либо частичную receipt. Такой остаток не объявляется успешной управляемой выдачей.

Поддержанный повтор не ремонтирует bootstrap, не усыновляет matching native grant и не восстанавливает недостающую receipt по публичному префиксу. Повтор остановится на incomplete или неподдержанной записи. Наличие этого ограничения требуется учитывать при выборе транспортного протокола и изоляции процессов.

Полностью сохранённая receipt позволяет восстановить потерянный ответ. Это отдельный случай: историческая выдача уже имеет проверяемую управляющую запись. Успех обычной демонстрации сам по себе не устанавливает обработку всех прерываний, ошибок хранения или потери питания.

## 8. Отзыв issuer

Административный credential может необратимо отозвать свой issuer. Сохраняется отдельный marker; прежние policy и grant остаются неизменными. Точный revoke replay требует тот же credential и operation ID; новый operation ID конфликтует с уже сохранённым отзывом.

Отзыв блокирует новые управляемые выдачи. Уже выданные native permits продолжают действовать по своим прежним правилам: holder может выполнять защищённый вызов и самостоятельно отозвать свой permit. Issuer marker не меняет их собственные markers и не устанавливает глобальный запрет principal или worker.

Исторический issue replay после отзыва issuer разрешён при правильном административном credential, том же holder credential и точном запросе. Он возвращает прежнюю complete receipt, не создаёт native grant, не удаляет marker и не оживляет issuer.

Повтор create также возвращает прежний immutable issuer при совпадении administrator, policy, credential и operation ID; существующий marker при этом проверяется и сохраняется. Новый запрос с другой политикой требует нового issuer directory.

## 9. Чтение и исторический replay

`show` issuer читает собственные grant и необязательный marker. Active означает отсутствие сохранённого отзыва этой записи; это не подтверждает текущее владение ключом или наличие pending work.

`receipt` читает только собственные controls issuance и не требует credential. Полные anchors проверяются структурно, без обращения к живым issuer, native permit или action. Они не позволяют реконструировать отсутствующие полные частные bodies.

Управляемый issue replay требует живой issuer и правильный admin credential. Он сравнивает immutable issuer binding и точный запрос с receipt, но не читает живые native permit/action. Поэтому исторический ответ может повториться при их отсутствии; собственный `receipt` reader не нуждается и в доступном issuer.

Ответ issuance стабилен целиком. Новая отметка текущего времени в replay не добавляется. `policy_authorized=true` описывает записанную локальную выдачу по захваченному правилу. Этот признак не устанавливает текущую допустимость новой выдачи, состояние child permit, аутентификацию читателя receipt либо автора файлового эффекта review.

## 10. API и CLI

Модуль `agentwiki.workspace_restore_case_retention_review_write_issuer` предоставляет:

~~~python
create_review_write_permit_issuer(issuer, operation_id=operation_id,
                                administrator=administrator,
                                credential=admin_credential, policy=policy)
inspect_review_write_permit_issuer(issuer)
revoke_review_write_permit_issuer(issuer, operation_id=operation_id,
                                credential=admin_credential)
issue_review_write_permit(issuer, issuance, permit, action,
                         operation_id=operation_id, principal=principal,
                         issuer_credential=admin_credential,
                         credential=holder_credential)
inspect_review_write_permit_issuance(issuance)
~~~

Пример ниже использует условный ledger ID; для реальной выдачи требуется identity нужного ledger. Частный родительский каталог должен уже существовать.

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-issuer create \
  --issuer /tmp/agentwiki-private/issuer-a \
  --operation-id issuer-a-01 --administrator local-administrator \
  --credential-out /tmp/agentwiki-private/issuer-a.credential \
  --rule fill-a local-holder-a local-worker-a \
    wrrwlgr_0123456789abcdef0123456789abcdef 1 prepared
~~~

CLI принимает повторяемый `--rule RULE_ID PRINCIPAL WORKER LEDGER_ID INDEX BEFORE_STATE` и выводит target из разрешённой таблицы переходов. Для точного create replay используется `--prepared-credential` с тем же запросом.

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-issuer issue \
  --issuer /tmp/agentwiki-private/issuer-a \
  --issuance /tmp/agentwiki-private/issuance-fill \
  --permit /tmp/agentwiki-private/permit-fill \
  --action /tmp/agentwiki-private/action-fill \
  --principal local-holder-a --operation-id issue-fill-01 \
  --issuer-credential-file /tmp/agentwiki-private/issuer-a.credential \
  --holder-credential-out /tmp/agentwiki-private/holder-fill.credential
~~~

Подготовленный holder credential при точном issue replay передаётся через `--prepared-holder-credential`. Новый файл не перезаписывает существующий. Секреты сохраняются и fsync до вызова регистрации; отдельного атомарного commit credential-файлов с controls нет.

~~~sh
python3 -m agentwiki workspace-restore-case-retention-review-write-issuer show \
  --issuer /tmp/agentwiki-private/issuer-a
python3 -m agentwiki workspace-restore-case-retention-review-write-issuer receipt \
  --issuance /tmp/agentwiki-private/issuance-fill
python3 -m agentwiki workspace-restore-case-retention-review-write-issuer revoke \
  --issuer /tmp/agentwiki-private/issuer-a --operation-id revoke-issuer-a-01 \
  --credential-file /tmp/agentwiki-private/issuer-a.credential
~~~

Собственно исполнение выданного native permit остаётся прежней командой `workspace-restore-case-retention-review-write-permit execute`. Новая issuer-команда не выполняет action step и не записывает lease history.

## 11. Частные форматы

Issuer хранит manifest, собственный постоянный пустой lock и после отзыва отдельный marker:

| Control | Формат |
| --- | --- |
| `.agentwiki-restore-case-retention-review-write-issuer.json` | Issuer12 |
| `.agentwiki-restore-case-retention-review-write-issuer.lock` | Пустой owner-lock |
| `.agentwiki-restore-case-retention-review-write-issuer.revoked.json` | Revocation7 |
| `.agentwiki-restore-case-retention-review-write-issuance.json` | Issuance15 в отдельном каталоге |
| `.agentwiki-restore-case-retention-review-write-issuance.lock` | Пустой owner-lock issuance |

Issuer12: `schema_version`, `rules_version`, `issuer_id`, `operation_id`, `created_at`, `issuer_path`, `parent`, `issuer_directory`, `administrator`, `credential_hash`, `policy`, `request_digest`.

Revocation7: `schema_version`, `rules_version`, `issuer_id`, `operation_id`, `revoked_at`, `grant_digest`, `request_digest`. Время отзыва не предшествует created_at issuer. Без общего registry нет проверки времени относительно всех выданных receipts; глобальную монотонную историю этот формат не заявляет.

Issuance15: `schema_version`, `rules_version`, `issuance_id`, `operation_id`, `created_at`, `issuance_path`, `parent`, `issuance_directory`, `issuer_anchor`, `issuer`, `rule`, `permit_anchor`, `permit`, `request`, `request_digest`.

Request4: `action_path`, `permit_path`, `principal`, `holder_credential_hash`. Issuer anchor4: `issuer_path`, `issuer_directory`, `issuer_record`, `issuer_lock`. Native permit anchor4: `permit_path`, `permit_directory`, `permit_record`, `permit_lock`. Directory pins статические, pins записей и пустых locks полные. Marker отзыва не включается в immutable anchor grant.

`schema_version=1`, rules version — `workspace-restore-case-retention-review-write-issuer-1`. Issuer ID имеет вид `wrrwpir_` с 32 шестнадцатеричными символами, issuance ID — `wrrwpis_` с тем же суффиксом. Native permit сохраняет прежние rules, ID и private grant13.

Все записи канонические, создаются эксклюзивно `O_EXCL` с owner0600 и fsync файлов/каталогов. Частные directories имеют owner0700. CLI требует прямые sibling `*.credential`, regular owner0600, одну ссылку, отсутствие symlinks и стабильные metadata при чтении не более 256 байтов. Literal bearer не передаётся аргументом shell.

Хеши credentials, anchors, request digests и управляющие bodies не публикуются. Контрольные суммы подтверждают целостность поддержанного локального формата; внешнюю подпись или защиту от доверенного владельца они не создают.

## 12. Публичные формы и flags

Числа обозначают поля верхнего уровня, включая общие 14 flags.

| Ответ | Собственные поля |
| --- | --- |
| Issuer20 | `rules_version`, `issuer_id`, `operation_id`, `created_at`, `administrator`, `policy` |
| Show18 | `rules_version`, `issuer`, `state`, `revocation` |
| Issuance22 | `rules_version`, `issuance_id`, `operation_id`, `created_at`, `issuer`, `rule`, `permit`, `policy_authorized` |

Show state — active либо revoked. При active revocation равна null. При revoked revocation5 содержит `rules_version`, `issuer_id`, `operation_id`, `revoked_at`, `reason=administrator_revoked`. У revocation5 нет общих flags.

Общие flags: `advisory_only=true`, `inspection_only=true`, `domain_writes_allowed=false`, `original_files_modified=false`, `archival_authorized=false`, `deletion_authorized=false`, `actor_authenticated=false`, `old_authority_activated=false`, `external_fencing_established=false`, `worker_authenticated=false`, `local_capability_only=true`, `external_identity_verified=false`, `issuance_policy_only=true`, `existing_permits_revoked=false`.

Это собственные признаки уровня выдачи. Вложенный native permit — прежний grant22 со своими прежними 16 flags; они не заменяются новыми. Issuance требует точного membership правила в захваченной issuer policy, точного совпадения scope с native action и времён issuer ≤ permit ≤ issuance.

`operation_id` issuance совпадает с `operation_id` созданного native permit: оба записываются из одного управляемого запроса. Глобальной уникальности этого ID между разными каталогами такое совпадение не устанавливает.

## 13. Работа множества агентов с документацией

Issuer policy позволяет заранее описать несколько конечных маршрутов выдачи: какие локальные principal получают permits для какого worker, позиции ledger и фазы. Администратор может выдать несколько native permits, а workers затем обращаются к независимым action/permit controls. Разные правила не создают новых системных пользователей и не доказывают независимость участников.

Один issuer lock сериализует управляемую выдачу и её отзыв. После выдачи native permits следуют прежним блокировкам; разные позиции могут ожидать общий ledger lock. Поэтому число правил, directories или child processes не является измерением полезной параллельности либо предельного числа LLM.

Для практического портала нужны наблюдаемые показатели: время ожидания допуска, время ожидания каждой lock, число новых выдач и исторических повторов, доля отказов по scope, число завершённых полезных изменений и стоимость их рассмотрения. Измерение нагрузки следует выполнять на отдельном согласованном наборе задач; обычный пример не заменяет такой эксперимент.

Предметный поток может отдельно связывать предложение, источники, изменения документов и кода, read/query-зависимости, актуальный review и принятие. Issuance добавляет локальное основание выдачи управляющего допуска в этом потоке. Она не проверяет истинность прозы, полноту источников, соответствие тексту поведения программы или внимательность reviewer.

## 14. Следующие границы развития

Дальнейший шаг требует выбрать обязательное место проверки полномочий и изолировать остальные пути записи. Для внешних участников понадобятся transport, источник identity, управление секретами и точное разделение доверенных процессов. Изменение политики потребует явной истории поколений; текущий immutable issuer не является такой историей.

Отдельно следует исследовать поддержанные окна native grant → issuance receipt, потерю ответа и прерывания, сохраняя различие между завершённой выдачей и неподдержанным остатком. Широкое восстановление нельзя выводить из наличия fsync либо обычного успешного запуска.

Для содержания технической вики остаются предметная опись, источники, совместное рассмотрение связанных изменений и независимое оценивание качества. Локальные bearer credentials и стабильные квитанции не предоставляют смыслового одобрения автоматически.

## 15. Выполненный обычный запуск

Обычный `development-01` завершён с первой попытки **22 октября 2026 года в 19:10:45.381801 +02:00, Europe/Berlin**. В отдельный пример экспортированы **449 публичных файлов: 430 JSON и 19 Markdown**, побайтно равных файлам этого завершённого запуска. Шесть новых stages, десять data JSON и два отчёта добавили 18 файлов к прежним 431.

| Наблюдение | Результат |
| --- | --- |
| Новые CLI-команды | 89: 69 успешных, 20 ожидаемых отказов |
| В том числе issuer CLI | 50: 33 успешных, 17 ожидаемых отказов |
| Вся заново выполненная цепочка | 761 обычный CLI и 14 унаследованных interrupt workers, всего 775 дочерних процессов |
| Новые issuers и правила | 2 issuers, 4 точных правила |
| Отзыв issuers | Оба отозваны, новых active issuers нет |
| Новые выдачи и permits | 3 issuance receipts, 3 native permits, все три permits остаются active |
| Новые actions и результаты | 3 intent и 3 saved result; выполнены три файловые фазы |
| Новые credential-файлы | 5: два административных и три holder |
| Настоящий полный ответ link issuance | Отброшен после успешной выдачи и восстановлен точно |
| Временно отсутствующие пути | Максимум 11; все возвращены в `finally` |

Шестой write job сохранил точный прежний minimum plan и defer от самозаявленного `simulated-issuer-review-operator`. Пятые catalog и ledger закрепили этот job; одна lease поколения 1 сохранила только claim и release. Issuer issue не выполнял action и не продлевал lease. Три native permit executions провели job через prepared → ready → linked → completed.

Неверный административный credential, незаявленный principal и правило с другим worker блокировали выдачу без резервирования child directories. Holder credential не получил административных прав. Изменённые параметры точного issue replay конфликтовали; новая issuance не усыновила существующий native permit. Повторяющиеся rule ID и одинаковые scopes не создали новые issuers.

Все три native permits были выданы до отзыва основного issuer. После отзыва новая выдача отказала, исторический issue replay сохранил прежнюю receipt, а ранее выданный finish permit выполнил linked → completed со своим holder credential. Повтор create/revoke не активировал issuer повторно. Эти наблюдения подтверждают выбранную границу будущей выдачи в данном обычном сценарии.

После release и отсутствия четырёх live bindings сохранился cached native finish replay. Когда также отсутствовали три native permits и три actions, точные issue replays читали receipts при живом отозванном issuer и правильных ключах. После скрытия самого issuer собственный receipt lookup работал при одиннадцати отсутствующих путях. Все пути восстановлены в обратном порядке; старые source/live наблюдения сохранили R4/36 и R5/59.

Исходные metadata для сравнения захвачены после Store initialization и первоначальных наблюдений. Demo читал проверенные публичные ответы, credential markers и `lstat` metadata; частные управляющие bodies, lease SQL и staging он не разбирал. Новых fault workers, инъекций неполных записей, ожидания expiry или ремонта данных нет. Гонка issue/revoke, отказ в промежутке native grant → receipt, потеря питания и откат часов отдельно не демонстрировались. Число PID не измеряет одновременную ёмкость LLM или полезность смыслового review.

Публичные основания:

- [Читаемый отчёт](../../examples/workspace-restore-case-retention-review-write-issuer/results/restore-case-retention-review-write-issuer-report.md) и [машинный отчёт](../../examples/workspace-restore-case-retention-review-write-issuer/results/restore-case-retention-review-write-issuer-report.json).
- [Issuers, политики и отзывы](../../examples/workspace-restore-case-retention-review-write-issuer/results/restore-case-retention-review-write-issuer-issuers.json).
- [Три issuance receipts](../../examples/workspace-restore-case-retention-review-write-issuer/results/restore-case-retention-review-write-issuer-issuances.json).
- [Исполнение и offline-наблюдения](../../examples/workspace-restore-case-retention-review-write-issuer/results/restore-case-retention-review-write-issuer-execution-observations.json).
- [Восстановление полного ответа](../../examples/workspace-restore-case-retention-review-write-issuer/results/restore-case-retention-review-write-issuer-reply-recovery.json).
- [89 CLI](../../examples/workspace-restore-case-retention-review-write-issuer/results/restore-case-retention-review-write-issuer-cli-outcomes.json) и [20 ожидаемых отказов](../../examples/workspace-restore-case-retention-review-write-issuer/results/restore-case-retention-review-write-issuer-rejections.json).

## 16. Отдельные проверки завершённого результата

После экспорта выполнены двенадцать отдельных read-only аудитов. Каждый прошёл при первом исполнении. Продуктовые команды для этих сверок не запускались, частные controls не ремонтировались, SQLite открывалась для чтения с `mode=ro&immutable=1` и `query_only` там, где проверялась её схема и история.

| Область | Подтверждённые наблюдения |
| --- | --- |
| Issuer и отзыв | 2 issuer12 и 2 revocation7, точные политики, IDs, canonical digests, modes и постоянные пустые locks |
| История выдачи | 3 issuance15, три пары issuer/permit anchor4, request4 и точные разрешённые scopes |
| Native permits | 3 новых grant13 без отзыва; разные admin/holder хеши соответствуют пяти новым private credential-файлам |
| Новая цепочка записи | 3 intent15/result6 и три выполненные фазы; write13, completed review14 с точной реконструкцией plan14, catalog11/member6 и ledger11 с двумя SQL operations |
| Привязки и сохранность | Полные pins и canonical digests совпали; inode окончательного single-link review сохранён, staging отсутствует; private bytes и проверенные metadata после чтения равны исходным |
| Публичные формы | В новой части сопоставлены 68 появлений issuer20, 23 show18, 19 revocation5 и 37 issuance22 |
| Публичный сценарий | 449 файлов, strict-разбор 430 JSON и 19 Markdown, 427 ссылок результатов, 775 различных дочерних PID; все 89 новых команд, 100 summary-полей и полные payloads шести stages согласованы |

Счётчики публичных появлений включают повторённые и вложенные объекты. Они относятся к двум issuers и трём выдачам; число представлений не увеличивает число участников, выполненных фаз или независимых экспертных оснований.

Проверенное частное основание содержит семь native permits с тремя прежними отзывами, двенадцать action intents с десятью результатами и двумя pending, шесть write jobs, восемь reviews, пять catalogs, пять lease DB и двадцать lease events. Предыдущие subsystem audits сохранили свою область; новые issuer-объекты отдельно проверены собственным аудитором. Integrity и foreign keys SQLite прошли, старые неполные controls не достраивались.

Общая privacy/preservation-сверка подтвердила побайтовое сохранение **5627 прежних защищённых файлов** и **449 экспортированных копий**. Разобраны 5684 JSON и 107 Python-исходников; 40 прежних модулей и десять значений `SCHEMA` не изменились. В 6244 файлах публикации не обнаружены 460 известных credentials, их хеши и 396 частных контрольных хешей. Эти секреты и частные записи в Git не экспортированы.

Проверка исторических доказательств сопоставила пять полных packet bodies, четыре transitions, шесть content policies, восемь governance policies, один audit root и три checkpoint edges/scopes. Подтверждены 3510 acceptance/continuity references, включая повторные представления; состояния checkpoints — два pending и один history_complete. В общем экспорте пересчитаны адреса 226 packet instances, 3510 continuity chains, 228 transitions, 11 audit headers и 13 checkpoints.

Фокусированный публичный обзор отдельно разобрал 17 новых JSON и один Markdown, разрешил 18 ссылок и сопоставил complete issuance replies, все команды, closed summary и шесть полных stage payloads. Проверки дали основания для описанного локального сценария и форматов. Они не устанавливают внешнюю идентичность, независимость reviewers, смысловую истинность документов, измеренную ёмкость агентов или сохранность при неподдержанных отказах.
