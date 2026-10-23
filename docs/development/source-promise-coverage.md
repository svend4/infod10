# Связь объявленных обещаний с проверенным происхождением значений

Сорок второй этап agentwiki связывает явно названные цитаты документа с выбранными сравнениями исходника, адаптера и JSON-поля. Входом служит полный сохранённый `source-adapter-1` report и отдельная авторская опись promises. Выход различает привязку цитаты, соответствие объявленных селекторов и результат сравнения значений.

Это совещательный материал редактора. Он не записывает решения, не меняет Store и прежние content gates, не предоставляет права принятия или публикации. `prose_semantics_verified=false` сохраняется даже при полном покрытии выбранной описи.

- [Прикладной сценарий](../../examples/source-promise-coverage/README.md).
- [Предыдущий source adapter](source-adapter.md).
- [Прежнее покрытие обещаний](promise-coverage.md).
- [Корпус urllib3 Retry 2.5.0](../../corpora/urllib3-retry-v2.5.0/README.md).
- [Дорожная карта](roadmap.md).

## 1. Какой пробел закрывается

Source adapter сравнивает явно выбранные AST-объявления с литералами адаптера и JSON-полями документа. Само совпадение этих трёх значений ещё не показывает, какое предложение свободного текста автор связывает с результатом. ID связи без её полного описания также не устанавливает область и предмет сравнения.

Новый слой требует точную цитату из захваченного документа и точное описание requirement: область, тип и три селектора. Он показывает, найдена ли цитата однозначно, находится ли она вне поддерживаемых fenced blocks, совпадает ли requirement с выбранным mapping и что известно о значениях.

Смысловая связь цитаты с requirement остаётся заявлением автора описи. Например, наличие числа 10 в сигнатуре не устанавливает единицу `retries`, а совпадение набора методов не доказывает универсального запрета повторять POST. Эти оговорки прежнего корпуса сохраняются.

## 2. Почему используется отдельный формат

Прежний `coverage-1` закреплён на `obligations-1`, review-set, proposal и artifact IDs. Source adapter имеет другое основание: три полных текста, profile и синтаксические endpoints. Новый инструмент не создаёт искусственный review-set и не выдаёт исторический AST report за runtime proposal.

Файлы прежних анализаторов и сохранённые результаты остаются неизменными. Для нового инструмента используется отдельная версия правил `source-promise-coverage-1`. Старые inventories автоматически не конвертируются; автор явно создаёт новую опись для точного parent report.

## 3. Источник и привязка inventory

Inventory имеет ровно четыре поля:

```text
schema_version: 1
source_report_id: sad_ + 64 hex
source_report_digest: 64 hex
promises: список от 0 до 100 записей
```

ID и digest должны совпасть с полным валидированным source-adapter report. Сначала проверяется весь parent по сохранённым правилам, затем точная привязка описи. Другой report с такими же mapping IDs либо равными значениями не подходит автоматически.

Пустая опись является допустимым входом. Её наблюдение блокируется с `no_declared_promises`; обе доли равны null. Отсутствие объявленных обещаний не превращается в полное покрытие документа.

Каждый promise содержит ровно пять полей:

| Поле | Назначение |
|---|---|
| `id` | Уникальный ID записи в описи |
| `description` | Авторское описание до 2000 символов |
| `anchor` | Объект с единственным полем `quote`, 1–4000 символов |
| `requirement` | Полное ожидаемое описание связи |
| `mapping_ids` | От 0 до 16 различных ID mappings parent report |

Requirement имеет ровно пять полей: `scope`, `value_type`, `source_selector`, `adapter_symbol`, `document_selector`. Они используют строгую грамматику прежнего source adapter. ID и annotation mapping в requirement не включаются; полный parent, включая annotation и исходные тексты, остаётся закреплён его digest.

Для выбранного default конструктора requirement может иметь вид:

```json
{
  "scope": "urllib3 2.5.0 Retry constructor total default expression",
  "value_type": "integer",
  "source_selector": {
    "kind": "parameter_default",
    "class_name": "Retry",
    "method_name": "__init__",
    "parameter": "total"
  },
  "adapter_symbol": "TOTAL_DEFAULT",
  "document_selector": {
    "fence_id": "retry-defaults",
    "field": "total_default"
  }
}
```

Это описание подходит только parent mapping с теми же пятью полями. Изменение scope, типа либо любого селектора не исправляется похожим ID или совпавшим значением 10.

## 4. Точная цитата и граница fenced blocks

Цитата ищется только в `source_report.captured.texts.document`. Учитываются все точные вхождения, включая перекрывающиеся. Число вхождений вычисляется до исключения code fences:

- Ноль: `missing_promise_quote`.
- Больше одного: `ambiguous_promise_quote`.
- Одно: сохраняются точные snippet, UTF-8 digest и character location.

Если один экземпляр цитаты находится в обычном тексте, а другой в fenced block, результат остаётся неоднозначным. Инструмент не выбирает удобное вхождение за автора.

Для единственного вхождения проверяется отсутствие любого пересечения с поддерживаемым fenced block. В интервал входят строки открывающего и закрывающего delimiters. Незакрытый блок покрывает весь оставшийся хвост документа.

Грамматика ограничена: от 0 до 3 начальных пробелов, не менее трёх backticks либо tildes; закрытие тем же символом с длиной не меньше открытия и только trailing whitespace. Внешние fences учитываются, вложенный похожий marker внутри открытого блока не выбирается как новый блок. Backtick info string, содержащая backtick, не открывает fence.

Проверяемое свойство называется **`outside_fenced_blocks`**. Это не классификатор всей прозы: HTML, inline code и indented code этим правилом не распознаются. Нахождение вне таких fences не доказывает, что цитата является обещанием либо соответствует JSON-полю по смыслу.

Anchor содержит девять полей:

```text
quote, quote_sha256, document_sha256, occurrence_count,
snippet, location, outside_fenced_blocks, resolved, reasons
```

Для отсутствующей либо неоднозначной цитаты snippet/location/outside равны null. Для единственной цитаты внутри или поперёк fence snippet и location сохраняются, `outside_fenced_blocks=false`, `resolved=false`, reason — `quote_in_fenced_block`. Для единственной внешней цитаты оба флага true.

Location содержит `start_offset`, `end_offset`, `start_line`, `end_line`. Offsets — в символах Python string, строки начинаются с 1; исходные CRLF сохраняются. Hashes и offsets закрепляют фактический текст, без удостоверения его автора или истинности.

## 5. Проверка документа и mapping references

Document endpoint извлекается независимо по requirement document selector и value type из того же captured document. Используется прежняя грамматика source adapter: единственный точный JSON fence и прямой ключ объекта. Это выполняется и при неизвестных либо ошибочных mapping references.

`anchor_verified` означает одновременно разрешённую внешнюю уникальную цитату и разрешённый document endpoint. Оно не означает смыслового подтверждения отношения между ними.

Каждая reference получает пять полей:

```text
mapping_id, valid, actual_requirement_digest, comparison, reasons
```

| Reference | Результат |
|---|---|
| Неизвестный ID | `valid=false`, actual digest/comparison null, `unknown_mapping_id` |
| Известный ID, другое requirement | `valid=false`, фактический digest и полный Comparison4 сохранены, `mapping_requirement_mismatch` со списком полей |
| Известный ID, полное совпадение metadata | `valid=true`, фактический digest и полный Comparison4 |

Сравниваются все пять requirement полей по canonical данным. Unknown ID разрешён структурой inventory, но остаётся наблюдаемой ошибкой привязки. Инструмент не перебазирует опись, не исправляет references и не выбирает первый подходящий результат.

Любая неверная reference делает row `invalid_mapping`, даже если рядом имеется верная. Без references row получает `uncovered`. Только непустой полностью верный список даёт `mapped`.

Profile предыдущего этапа запрещает повтор одинаковых selector triples. Поэтому несколько promises могут использовать один mapping, но добавление references не создаёт несколько независимых проверок того же requirement.

## 6. Покрытие и удовлетворённость — отдельные выводы

Promise считается структурно covered при `anchor_verified=true` и `mapping_state=mapped`. При этом сравнение значений может дать mismatch или unresolved. Доля покрытия поэтому не является долей истинных обещаний либо разрешением публикации.

`satisfaction_state` равен `not_evaluated`, если mapping не установлен. Для mapped row он равен mismatch при наличии reference с Comparison4 state mismatch; иначе unresolved при unresolved state; иначе match. Все три pair результата сохраняются отдельно.

В частности, source adapter может иметь:

```text
source_adapter: unresolved
source_document: unresolved
adapter_document: mismatch
state: unresolved
```

Такое наблюдение остаётся satisfaction unresolved, а известный adapter/document mismatch остаётся видимым в полном Comparison4. Результат не подменяется одним общим зелёным или красным флагом.

Coverage state принимает `no_declared_promises`, `declared_promises_covered` либо `declared_coverage_incomplete`. Общий state независимо принимает `blocked` или `current_for_human_review`.

## 7. Счётчики, повторы и upstream blockers

Counts содержат 15 полей: total, assigned, mapped, anchor_verified, covered, matching, mismatch, unresolved, uncovered, invalid_mapping, unresolved_anchor, unique_requirements, covered_unique_requirements, assigned_mapping_ids, mapped_mapping_ids. Matching/mismatch/unresolved считают только covered rows по satisfaction.

`coverage_fraction` делит covered promises на все объявленные promises. `unique_requirement_fraction` считает distinct requirement digests. При пустой описи обе доли null. Процент полноты всей документации не вычисляется.

`shared_mappings` показывает ID, которыми пользуется больше одной записи, с полными и valid списками promise IDs. `duplicate_requirements` показывает повторённые requirement digests. Порядок promise IDs сохраняет порядок inventory, группы сортируются по mapping ID либо digest. Эти группы сами по себе не блокируют результат и не превращают повторы в независимые свидетельства.

Отдельно видны unknown mapping IDs и parent mapping IDs без единой valid reference. Наличие valid reference здесь не зависит от пригодности цитаты.

Все parent blockers копируются без фильтрации в начало общего списка. Parent state также сохраняется. Затем добавляются `no_declared_promises`, причины неразрешённых anchors, uncovered/invalid mapping и неудовлетворённых mapped rows в порядке inventory.

**Исключение проблемного mapping из описи не снимает upstream blocker.** Поле `upstream_comparisons` всегда показывает Comparison4 всех parent mappings в прежнем profile order, включая неиспользованные. Можно получить coverage 1/1 по здоровому полю и одновременно общий blocked из-за другого parent mapping.

## 8. Сохранённый отчёт и историческое show

Full report имеет девять полей:

```text
schema_version, rules_version, created_at, source_report, inventory,
observation, limits, report_id, report_digest
```

Report ID — `spc_` плюс canonical digest всех полей кроме ID и самого digest. `source_report` включает полный валидированный parent по его прежним правилам, inventory хранится полностью. UTC created_at создаётся при первоначальном анализе; clock parent остаётся отдельным историческим временем.

Observation содержит 22 поля: parent ID/digest/state/blockers/comparisons, coverage state/counts/fractions/results, shared/duplicate groups, unmapped/unknown IDs, blockers/state и шесть flags:

```text
requires_human_review=true
auto_accept=false
live_state_checked=false
runtime_binding_verified=false
prose_semantics_verified=false
atomic_capture=false
```

Load/validate проверяет закрытые schemas, parent целиком, inventory binding, clocks, digest/ID и заново вычисляет всю observation из captured данных по фиксированной версии правил. Живые исходники, profile, inventory file, runtime DB и сеть для show не требуются. Встроенные в данные пути не открываются.

Analyze и show возвращают одинаковую closed summary из 25 полей: report ID/digest/rules и полную observation. В ней намеренно видны цитаты, небольшие endpoints и все parent comparisons. Новое время и mutable current status в summary не появляются.

API модуля [source_promise_coverage.py](../../agentwiki/source_promise_coverage.py): `analyze_source_promise_coverage`, `validate_inventory`, `load_inventory`, `validate_report`, `load_report`, `save_report`, `summary`. Команды зарегистрированы в [source_promise_coverage_cli.py](../../agentwiki/source_promise_coverage_cli.py).

Это самодостаточный исторический материал с внутренней проверкой согласованности. Он не является внешней подписью, независимым заверением или доказательством текущего состояния проекта.

## 9. Файлы, пределы и CLI

| Ресурс | Предел |
|---|---:|
| Full report |5000000 bytes |
| Inventory |256000 bytes |
| Promises |0–100 |
| References на одну запись |0–16 различных IDs |
| Quote |1–4000 символов |
| Description |1–2000 символов |
| JSON nodes / depth |200000 / 64 |
| Integer |256 бит |

Предел outer report сохраняется независимо от размера parent: максимальный допустимый parent не обязательно поместится вместе с новой оболочкой. UTF-8, finite numbers, duplicate JSON keys и закрытые поля проверяются строго. Данные копируются, входные объекты не изменяются.

CLI читает только явно указанные bounded regular files без symlink, проверяя стабильность capture. Сохранение эксклюзивное: существующий output не перезаписывается. Общая транзакция файловой системы, ремонт частичного output и гарантии всех аппаратных отказов не добавляются.

```sh
python3 -m agentwiki source-promise-coverage analyze \
  --source-report "$SOURCE_REPORT_JSON" \
  --inventory "$EXPLICIT_INVENTORY_JSON" \
  --output /tmp/source-promise-report-01.json

python3 -m agentwiki source-promise-coverage show \
  --input /tmp/source-promise-report-01.json
```

Inventory нужно явно подготовить для точного parent ID/digest. Безопасные ошибки: `source_promise_coverage_invalid`, `source_promise_coverage_io_failed`, `unknown_source_promise_coverage_command`. Необработанные paths, captured data и исключения в stderr не выводятся.

## 10. Согласованный обычный сценарий

Новый demo независим от прежней recovery-цепочки и не повторяет source-adapter demo. Он копирует три уже сохранённых публичных parent reports: baseline, adapter total=11 и mixed unresolved/mismatch. С ними сохраняется точная MIT license прежнего корпуса. Новые inventories явно оформлены авторами проекта; старая inventory автоматически не конвертируется.

| Случай | Проверяемое различие |
|---|---|
| Baseline | Две цитаты и две точные связи |
| Uncovered total | Отсутствующая reference остаётся uncovered |
| Invalid requirements | Unknown ID и другой scope не принимаются за нужную связь |
| Missing quote | Совпадение поля не восстанавливает отсутствующую цитату |
| Ambiguous quote | Частое слово Retry не выбирается произвольно |
| Fenced quote | Единственная цитата внутри JSON fence сохраняется, но не допускается |
| Shared duplicates | Три promises, два distinct requirements, один общий mapping |
| Empty inventory | Null fractions и blocked |
| Omitted parent mismatch | Покрытая здоровая запись не снимает другой upstream blocker |
| Mixed parent unresolved | Полное покрытие не скрывает unresolved и известный pair mismatch |

Методика предусматривает analyze/show для каждого из десяти случаев, отдельный show при временно скрытой inputs directory с finally-восстановлением и отказ при занятом output без изменения прежних bytes/metadata. Новая порча reports, fault workers, credentials, DB и выполнение captured Python не требуются.

Фактические artifact counts, child PID, exit codes и результаты выполненного запуска приведены в разделе 12. Размер последовательности CLI не обозначает предел числа агентов либо измеренную параллельность.

## 11. Значение для нескольких агентов

Автор документа, автор структурированного requirement и проверяющий могут рассматривать одну точную запись с сохранённым parent. Изменение ID, scope, цитаты или источника не переносит прежнее основание автоматически. Историческое show позволяет обсуждать один и тот же captured material без доступа к рабочим файлам другого участника.

Раздельные coverage и satisfaction полезны для распределения работы: одна запись требует восстановить цитату, другая — исправить привязку, третья — рассмотреть mismatch или неопределённость source projection. Общие mappings и дубликаты делают повтор работы видимым; они не доказывают независимости мнений и сами по себе не задают число требуемых reviewer.

Опись по-прежнему задаёт собственный знаменатель. Человек должен оценить пропущенные обещания, достоверность источника, смысловую связь цитаты с полем и пригодность scope. Даже полное declared coverage не устанавливает истинность свободной прозы, runtime behavior, актуальность upstream или право публикации.

Следующее предметное исследование — независимая разметка большего набора реальных обещаний и связей с исходником, учёт пропусков и труда проверки. Связывание таких материалов с действующей политикой принятия требует отдельного обоснованного протокола; новый report не включается в content gate неявно.

## 12. Выполненный обычный запуск

Первый запуск нового сценария завершился с exit 0 и пустым stderr. Время отчёта — **2026-10-23T17:10:42.446627+02:00**, Europe/Berlin; workspace — `/tmp/infod10-source-promise-coverage-development-01`. Переносимые основания: [читаемый отчёт](../../examples/source-promise-coverage/results/report.md), [машинная сводка](../../examples/source-promise-coverage/results/report.json), [все случаи и ответы analyze/show](../../examples/source-promise-coverage/results/case-observations.json).

Сохранён 31 recursive public file: 14 inputs (три прежних полных parent reports, десять новых inventories, одна MIT license), десять новых full reports и семь flat artifacts. Форматы: 29 JSON, один Markdown, одна лицензия. Все 31 файл скопированы из workspace byte-for-byte. Parent reports и license совпали с прежними публичными основаниями; весь прежний 26-файловый source-adapter example остался неизменным.

| Случай | Covered / total | Unique covered / total | Matching / unresolved среди covered | State |
|---|---:|---:|---:|---|
| Baseline |2/2 |2/2 |2/0 |current_for_human_review |
| Uncovered total |1/2 |1/2 |1/0 |blocked |
| Invalid requirements |0/2 |0/2 |0/0 |blocked |
| Missing quote |1/2 |1/2 |1/0 |blocked |
| Ambiguous quote |1/2 |1/2 |1/0 |blocked |
| Fenced quote |1/2 |1/2 |1/0 |blocked |
| Shared duplicates |3/3 |2/2 |3/0 |current_for_human_review |
| Empty inventory |null |null |0/0 |blocked |
| Omitted parent mismatch |1/1 |1/1 |1/0 |blocked |
| Mixed parent unresolved |2/2 |2/2 |1/1 |blocked |

В десяти авторских иллюстрациях 18 promise rows: 17 assigned, 15 mapped, 15 anchor_verified, 12 covered. Среди covered — 11 matching, ноль mismatch и одна unresolved; отдельно одна uncovered, две invalid_mapping и три unresolved_anchor. Повторяющиеся selected requirements и родители делают суммарные counts описанием иллюстраций, а не измерением полноты документации или качества независимой разметки.

Полное coverage описи само по себе не снимает блокировку: в omitted-parent-mismatch здоровая methods запись даёт 1/1, а исключённый total остаётся в upstream comparisons и blockers. Mixed-parent-unresolved сохраняет все четыре поля сравнения: source_adapter и source_document unresolved, adapter_document mismatch, итог unresolved. В 20 upstream comparisons — 18 match, один mismatch, один unresolved; во всех 60 pair outcomes — 55 match, три mismatch, две unresolved. Сохранены два upstream и восемь local blockers.

В 17 references — 15 valid и две invalid, включая одну unknown. Известная ссылка с неверным scope сохраняет actual requirement digest и полное сравнение, не создавая покрытия. Одна shared mapping group и одна duplicate requirement group видны явно. Уникальная цитата внутри JSON fence сохранила snippet/location с outside_fenced_blocks=false; в пустой описи обе fractions остались null.

[Журнал CLI](../../examples/source-promise-coverage/results/cli-outcomes.json) содержит 22 последовательных дочерних процесса с различными PID: 21 success и один ожидаемый source_promise_coverage_io_failed. Это 11 analyze (включая отказ) и 11 show. Десять analyze/show дали одинаковые полные summaries. Дополнительный [historical show](../../examples/source-promise-coverage/results/offline-observations.json) работал при отсутствии всей inputs directory; own report оставался online и неизменным, inputs возвращены в finally. [Отказ коллизии output](../../examples/source-promise-coverage/results/output-collision.json) не изменил bytes/metadata прежнего baseline report.

Первый preservation/privacy audit завершился с exit 0 и пустым stderr: 7592 прежних files byte-for-byte, 132 прежних tracked Python sources кроме ровно init/main integration, 55 специально закреплённых recovery/source-adapter modules, десять неизменённых schema literals. Строго прочитаны 7135 JSON, разобраны 122 agentwiki Python AST и проверены 8575 локальных Markdown links. В 7811 publication files не найдены 598 известных credentials с hashes и 457 private hashes. Прежний sealed runtime и 503 его публичные копии имеют отдельную область проверки; эти 503 не являются числом новых результатов.

Read-only proof audit завершился с exit 0 и пустым stderr: пять full packets, четыре transition bodies, шесть content policy и восемь governance policy versions, 3798 references, один audit root, три checkpoint links (два pending, один history_complete). Старые proof records остаются историческими основаниями прежнего сценария и не одобряют новые coverage reports. После окончательной редакции документации повторяется preservation/privacy audit и проверка всех 44 частей архива.

Новый независимый public audit также прошёл с первого исполнения: exit 0, stderr empty, 28561 проверка сохранённых данных. Без импорта продуктовых модулей он реконструировал десять новых полных отчётов, десять полных nested source-adapter parents и три отдельно скопированных parent reports. Повторно разобраны 60 nested и 18 copied parent endpoints, 18 promise anchors и 18 document endpoints; проверены все counts/fractions/groups, exact fixture derivations, canonical IDs/digests, полные comparisons и blockers. Сверены 31 byte-identical export, 29 strict JSON, 64 поля demo summary, 22 ordered CLI outcomes и 21 ссылка внутри report.md. Прежний standalone source-adapter audit driver не запускался: его неизменённая логика валидации используется только для 13 полных родителей в новой области. Число проверок не измеряет смысловую точность или независимость участников.

Новых credentials, DB, Store writes, LLM-вызовов и worker pool нет. Прежние demo и отдельные scoped audit drivers не повторялись. Новые tests/suites, fault workers, порча reports, нагрузочные измерения и проверка всех аппаратных отказов не проводились. Результаты ограничены десятью авторскими иллюстрациями и проверкой сохранённых оснований; независимая смысловая разметка и проверка полноты описи остаются следующей предметной работой.
