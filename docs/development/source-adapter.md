# Проверяемый адаптер исходника: выбранные значения документа и кода

Сорок первый этап agentwiki возвращает рассмотрение к содержанию технической документации. Новый read-only анализ связывает три явно выбранных представления: сохранённый исходник библиотеки, авторский Python-адаптер и JSON-поле документа. Код разбирается как данные через AST; ни библиотека, ни предлагаемый код не исполняются.

Это отдельный инструмент редактора. Его результат не меняет Store, прежние content gate и writer API, не записывает согласование и не предоставляет права принятия или публикации.

- [Прикладной сценарий](../../examples/source-adapter/README.md).
- [Корпус urllib3 Retry 2.5.0](../../corpora/urllib3-retry-v2.5.0/README.md).
- [Разбор реального контракта и его оговорок](real-contract-urllib3.md).
- [Прежние межфайловые обязательства](cross-file-obligations.md).
- [План развития](roadmap.md).

## 1. Какой разрыв рассматривается

В сохранённом корпусе urllib3 два значения перенесены вручную в `authored/retry_config.py`. Прежний экстрактор принимает простой модуль с прямыми литеральными присваиваниями. В upstream значение `Retry.DEFAULT_ALLOWED_METHODS` записано через `frozenset([...])` внутри класса, а `total=10` находится в сигнатуре конструктора. Поэтому равенство документа и адаптера ещё не проверяло сам перенос из исходника.

Новый анализ вводит два ограниченных source selector. Они извлекают выбранные синтаксические значения из захваченного текста и сравнивают их с адаптером и документом. Несовпадение видно отдельно от неподдержанной либо неоднозначной конструкции.

Исходная база — уже сохранённый коммит urllib3 `aaab4eccc10c965897540b21e15f11859d0b62e7`. Исторические URL, дата получения и hashes записаны в [манифесте корпуса](../../corpora/urllib3-retry-v2.5.0/manifest.json). На этом этапе использование этих bytes не означает нового обращения к сайту или проверки подписи тега. [Лицензия MIT](../../corpora/urllib3-retry-v2.5.0/upstream/LICENSE.txt) сохранена вместе с корпусом.

## 2. Три представления и явный профиль

Профиль содержит `schema_version: 1` и непустой список `mappings` из 1–16 записей. Одна запись имеет ровно семь полей:

| Поле | Назначение |
|---|---|
| `id` | Уникальный ограниченный ID связи |
| `scope` | Авторское описание области утверждения |
| `annotation` | Авторская оговорка или пояснение |
| `value_type` | `integer` либо `string_set` |
| `source_selector` | Выбранная конструкция upstream AST |
| `adapter_symbol` | Прямой символ простого Python-адаптера |
| `document_selector` | Один JSON fence и прямой ключ объекта |

Повторы ID и одинаковых троек source/adapter/document selector отвергаются. Scope и annotation сохраняются для рассмотрения; они не являются входом вывода значения или доказательством смысла прозы.

Пример профиля для двух выбранных значений:

```json
{
  "schema_version": 1,
  "mappings": [
    {
      "id": "default-methods",
      "scope": "Retry.DEFAULT_ALLOWED_METHODS в сохранённой версии urllib3 2.5.0",
      "annotation": "Набор по умолчанию; отсутствие POST не означает универсального запрета его повторов.",
      "value_type": "string_set",
      "source_selector": {
        "kind": "class_frozenset_strings",
        "class_name": "Retry",
        "symbol": "DEFAULT_ALLOWED_METHODS"
      },
      "adapter_symbol": "DEFAULT_ALLOWED_METHODS",
      "document_selector": {
        "fence_id": "retry-defaults",
        "field": "default_allowed_methods"
      }
    },
    {
      "id": "default-total",
      "scope": "Объявленный default параметра Retry.__init__.total в сохранённом исходнике",
      "annotation": "Смысловая единица retries взята из документации; AST integer сам её не устанавливает.",
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
  ]
}
```

## 3. Что именно понимает source selector

### 3.1. Класс и набор строк

`class_frozenset_strings` требует один прямой top-level `ClassDef` с выбранным именем. Выбранный класс не имеет bases, decorators, keywords или generic type parameters. Видимые другие bindings выбранного class name и очевидные внешние переприсваивания выбранного class attribute делают endpoint неразрешимым.

В классе выбирается ровно одно прямое `Assign` с одной целью `Name`. Выражение имеет строго форму `frozenset(<literal list, tuple or set of strings>)`: один positional argument, без keywords, starred arguments и comprehension. Повторы строк отвергаются до нормализации множества. Другая запись того же выбранного символа, включая `AnnAssign`, `AugAssign`, определение функции или импорт, не подменяется выбором первого подходящего присваивания.

Явная видимая смена binding `frozenset` также приводит к `unresolved`. При поиске lexical bindings рассматриваются expressions в defaults, decorators, return и argument annotations; nested function/class bodies не становятся частью внешней lexical scope. Для имени `frozenset` применяется дополнительная консервативная проверка всего AST. Разбор не интерпретирует условные определения и вложенные классы как прямую выбранную декларацию.

Даже при подходящей форме остаётся `runtime_binding_verified=false`. AST показывает синтаксис вызова имени `frozenset`; он не доказывает разрешение этого имени в выполняемой программе, отсутствие динамического изменения globals, фактическое создание класса или значение runtime attribute.

### 3.2. Default параметра

`parameter_default` выбирает один прямой `FunctionDef` внутри выбранного класса: без decorators, generic type parameters, дубликатов или видимой замены выбранного метода. Async-метод не принимается за обычный метод.

Параметры `posonlyargs + args` сопоставляются с positional defaults по их реальным позициям. Отдельно рассматриваются keyword-only defaults. Принимается точный `Constant(int)` либо unary minus целого литерала. `True`, `False`, `5 + 5`, вызов функции и ссылка на другой символ не получают значения integer через вычисление выражения.

Выбор синтаксического default не устанавливает, с какими аргументами приложение реально вызывает этот конструктор. Аннотации и тело метода не выполняются.

## 4. Адаптер и документ

Adapter endpoint сохраняет прежнюю строгую грамматику простого модуля: необязательный начальный docstring и прямые одиночные присваивания литералов. Imports, functions, calls, mutations и дубликаты присваиваний делают его неподдержанным. Типы integer и boolean различаются строго; повтор члена строкового множества не скрывается нормализацией.

Document endpoint выбирает один целый блок с маркером `json agentwiki:<fence_id>`, учитывая внешние Markdown fences. Затем strict JSON parser выбирает один прямой ключ объекта. Вложенный pointer, поиск по похожему имени или извлечение числа из свободной прозы не выполняются.

Отсутствующий, повторный или незакрытый fence, повтор JSON-ключа, отсутствие выбранного поля и несоответствие типа дают `unresolved`. Валидное отличие значений даёт `mismatch`. Синтаксическая ошибка Python не превращается в частично успешное извлечение другого участка того же текста.

## 5. Три попарных сравнения

Для каждой записи сохраняются source, adapter и document endpoints и три результата:

| Пара | Вопрос |
|---|---|
| `source_adapter` | Совпал ли выбранный перенос в адаптер? |
| `source_document` | Совпало ли выбранное поле документа с source projection? |
| `adapter_document` | Совпали ли адаптер и поле документа между собой? |

Для каждой пары `unresolved` означает, что хотя бы одна сторона не разрешена. Две разрешённые стороны дают `match` либо `mismatch` по canonical нормализованным значениям. Integer сравнивается строго, string set — после проверки повторов и сортировки.

Row state равен `unresolved`, если не разрешён любой endpoint; иначе он равен `mismatch` при любом несовпадении, иначе `match`. Уже установленный pair mismatch сохраняется и при row `unresolved`. Например, неподдержанный source не должен скрывать известное отличие adapter от document.

Для каждой non-match строки сохраняется один blocker в исходном порядке профиля: `mapping_unresolved` либо `mapping_mismatch`. Общий state — `blocked` при наличии blockers, иначе `current_for_human_review`. Counts содержат только `mappings`, `match`, `mismatch`, `unresolved`; пустая опись и процент «полноты документации» не вводятся.

## 6. Что сохраняет основание

Endpoint имеет ровно восемь полей: `resolved`, `value`, `normalized_value`, `sha256`, `snippet`, `location`, `selector`, `reasons`. SHA-256 связывает весь UTF-8 текст соответствующего input. Разрешённый snippet — точный slice текста по сохранённым offsets.

Location содержит `start_offset`, `end_offset`, `start_line`, `end_line`. Offsets считаются в символах Python string, строки начинаются с 1; CRLF сохраняется. UTF-8 byte columns AST переводятся в character offsets. Для class constant snippet охватывает присваивание, для parameter — default expression, для адаптера — присваивание, для документа — содержимое всего выбранного JSON-блока.

Неразрешимый endpoint может сохранить уже выбранные snippet/location и raw JSON value. Например, при boolean вместо integer сохраняется raw `True`, но `normalized_value` остаётся null и typed comparison не выполняется. При неподдержанном выражении default сохраняется его выбранный фрагмент, без вычисления значения. Такой частичный результат не обозначает `resolved=true`.

Причины содержат безопасные codes. Исходный SyntaxError, произвольные private paths и необработанные исключения не включаются в stderr.

## 7. Полный отчёт и историческое чтение

Full report имеет ровно восемь полей:

```text
schema_version, rules_version, created_at, captured,
observation, limits, report_id, report_digest
```

Rules version — `source-adapter-1`. Captured содержит точные три текста и исходный validated profile. Observation содержит counts/results/blockers/state и обязательные flags:

```text
requires_human_review=true
auto_accept=false
live_state_checked=false
runtime_binding_verified=false
```

`report_digest` — SHA-256 canonical JSON всех полей кроме ID и самого digest. `report_id` равен `sad_` плюс этот digest. Created_at добавляется при первоначальном анализе в UTC ISO с timezone.

`load_report` проверяет закрытые поля, schemas, ID/digest, clock и заново вычисляет observation по captured inputs и фиксированным rules/limits. Canonical равенство сохранённого и пересчитанного наблюдения обязательно. Живые source/adapter/document/profile не нужны, новое время при show не создаётся. Пути внутри данных не открываются.

CLI analyze и show возвращают одну и ту же компактную summary: `report_id`, `report_digest`, `rules_version`, `state`, `counts`, `results`, `blockers` и четыре flags. Selected snippets входят в публичную provenance намеренно. Полный report содержит публичные captured source data; это другой формат, чем частные recovery controls предыдущих этапов.

## 8. Файлы, ограничения и ошибки

CLI читает только явно переданные пути. Файлы должны быть regular, без symlink; read выполняется один раз с проверкой стабильности identity/size/mtime. Точные UTF-8 bytes и переводы строк сохраняются. Профиль или отчёт не могут направить reader к дополнительным файлам через встроенное имя пути.

| Ресурс | Предел |
|---|---:|
| Каждый source/adapter/document text |1000000 UTF-8 bytes |
| Profile |64000 bytes |
| Full report |5000000 bytes |
| Mappings |1–16 |
| JSON nodes / depth |200000 / 64 |
| AST nodes / depth |20000 / 64 |
| Integer |256 бит |
| Scope и annotation |По 2000 символов |

Duplicate JSON keys, nonfinite numbers, неверная схема и превышение общих bounds отвергаются. Неподдержанная выбранная конструкция даёт endpoint reason. Безопасные CLI errors: `source_adapter_invalid`, `source_adapter_io_failed`, `unknown_source_adapter_command`.

Save проверяет полный report и создаёт новый output эксклюзивно. Существующий файл не перезаписывается. Ремонт частично записанных outputs, общая транзакция файловой системы и гарантии всех отказов питания не заявляются.

## 9. Команды

```sh
python3 -m agentwiki source-adapter analyze \
  --source corpora/urllib3-retry-v2.5.0/upstream/retry.py \
  --adapter corpora/urllib3-retry-v2.5.0/authored/retry_config.py \
  --document corpora/urllib3-retry-v2.5.0/authored/contract.md \
  --profile /tmp/source-adapter-profile.json \
  --output /tmp/source-adapter-report.json

python3 -m agentwiki source-adapter show --input /tmp/source-adapter-report.json
```

`/tmp/source-adapter-profile.json` нужно явно создать по нужному профилю; наличие корпуса не включает новую политику автоматически. Для нового analyze задаётся незанятый output. Analyze сохраняет полный report, а stdout возвращает summary.

## 10. Согласованный обычный сценарий

Demo использует новый workspace и три копии уже закреплённых публичных текстов корпуса. Baseline source сначала связывается с manifest hash; все три baseline текста должны совпасть с прежними corpus bytes. Старый корпус и прежние результаты сохраняются.

Согласованы восемь обычных случаев: baseline; изменённый авторский TOTAL; вычисляемый source default; видимое затенение frozenset; повтор выбранного класса; неоднозначный JSON fence; boolean вместо integer; одновременно неразрешимый source и известный adapter/document mismatch. Изменённые входы — явно отдельные авторские fixtures, а не новые снимки upstream.

План демонстрации включает analyze/show, историческое show при временно скрытых трёх inputs и profile с их finally-восстановлением, а также отказ при занятом output без изменения его bytes. Искусственная порча report, новый fault worker, исполнение upstream и повтор 503-файлового recovery baseline не требуются.

Этот раздел описывает согласованную до исполнения методику. Фактические counts, exit codes и независимые аудиты приведены в разделе 12.

## 11. Несколько агентов и смысл документации

Агенты могут готовить разные адаптеры, профили связей и тексты. Анализ сохраняет точный предмет сравнения каждого предложения. Два автора, переписавшие одну ошибочную константу одинаково, больше не получают source match только за взаимное согласие adapter и document: source endpoint рассматривается отдельно.

Параллельные анализы разных outputs не требуют новой private DB или общего writer gate. Это свойство read-only задач, а не измерение максимального пула LLM, ускорения или независимости reviewer. Количество ordinary CLI children в demo не устанавливает capacity либо качество review.

Человек по-прежнему должен проверить выбор утверждений, смысловую область, достоверность источника и соответствие свободного текста полям. `total=10` не выводит единицу retries без документации и не обещает 11 сетевых обращений. Отсутствие POST в выбранном наборе не устанавливает универсальное «никогда не повторять POST»: оговорки корпуса сохраняются независимо от совпадения литералов.

Hashes устанавливают согласованность сохранённых bytes и вычисления. Они не удостоверяют автора, подпись upstream, независимую разметку, полноту документации и истинность произвольного пересказа. Этот этап создаёт проверяемое основание для обсуждения выбранного переноса; прежние полномочия принятия и внешняя публикация остаются отдельными действиями.

## 12. Фактический запуск и проверенные результаты

Первый ordinary demo завершился с exit code 0 и пустым stderr. Отчёт создан **2026-10-23T13:45:32.645060+02:00**, Europe/Berlin. Workspace этого экземпляра — `/tmp/infod10-source-adapter-development-01`; пул workers не создавался. Сценарий самостоятельно читает закреплённый публичный корпус и не повторяет прежнюю recovery-цепочку.

Основания: [отчёт](../../examples/source-adapter/results/report.md), [report JSON](../../examples/source-adapter/results/report.json), [указатели на восемь полных captured reports и ответы](../../examples/source-adapter/results/case-observations.json), [18 CLI outcomes](../../examples/source-adapter/results/cli-outcomes.json).

### 12.1. Восемь наблюдений

| Случай | Mapping match / mismatch / unresolved | Что наблюдалось |
|---|---:|---|
| Baseline |2 / 0 / 0| Выбранные значения совпали во всех трёх представлениях |
| Adapter total=11 |1 / 1 / 0| Адаптер отличается от исходника и документа |
| Source total=5+5 |1 / 0 / 1| Выражение не вычислялось |
| Shadowed frozenset |1 / 0 / 1| Видимое связывание имени блокирует source projection |
| Duplicate Retry |0 / 0 / 2| Выбор класса неоднозначен |
| Duplicate fence |0 / 0 / 2| Оба document endpoints неразрешимы |
| Source total=True |1 / 0 / 1| Boolean не принят за integer |
| Computed source и adapter=11 |1 / 0 / 1| Row unresolved сохранил отдельный adapter_document mismatch |

Baseline извлёк `Retry.DEFAULT_ALLOWED_METHODS` со значениями DELETE, GET, HEAD, OPTIONS, PUT, TRACE после нормализации и default `total=10`. Исходный class assignment расположен в строках 184–186, integer expression — в строке 204. Точные UTF-8 hashes и character spans сохранены в отчётах. Все три baseline input совпали с прежними corpus bytes; исходник и лицензия сопоставлены с прежним manifest, копия MIT notice включена в новый пример.

В восьми иллюстрациях получилось 16 mapping результатов: 7 match, 1 mismatch, 8 unresolved. Три пары на каждую запись дают 48 результатов: 29 match, 3 mismatch, 16 unresolved. Из 48 endpoints разрешены 40, не разрешены 8: 6 source и 2 document. Baseline имеет state `current_for_human_review`, остальные семь — `blocked`. Это повторное использование двух выбранных связей в авторских вариациях, без оценки точности на независимом корпусе.

Для вычисляемого default сохранены snippet `5 + 5` и его location, raw value и normalized value остаются null. Для boolean сохранены snippet `True`, location и raw value true; normalized value null, reason `value_type_mismatch`. Эти частичные свидетельства независимо восстановлены аудитором. В смешанном случае source_adapter/source_document равны unresolved, а adapter_document остаётся mismatch.

Каждый analyze возвращал тот же summary после show. Затем вся inputs directory временно скрыта: baseline show успешно пересчитал отчёт по captured texts/profile собственного файла, без живых inputs. Directory восстановлена в finally. Analyze в существующий baseline output получил ожидаемый `source_adapter_io_failed`; прежние report bytes и metadata не изменились. Повторного demo, ремонта либо изменения runtime не потребовалось.

Выполнены 18 ordinary CLI: 17 успешных, один ожидаемый отказ. Зафиксированы 18 различных PID; перекрытие процессов, пропускная способность и предел числа агентов не измерялись. Сохранены 26 публичных файлов: 11 inputs, 8 полных reports и 7 плоских artifacts; по форматам — 15 JSON, 3 Markdown, 7 Python data files, одна лицензия. Demo summary содержит 58 полей. Новых DB, credentials, Store writes и LLM-вызовов нет.

### 12.2. Три read-only аудита

Все три аудита с первого исполнения завершились с exit code 0 и пустым stderr. Их исходники заранее независимо просмотрены. Аудиторы не импортировали продукт и не выполняли продуктовые команды либо input Python.

| Аудит | Проверенное основание |
|---|---|
| Source adapter |8 полных reconstruction, 26 byte copies, 15 strict JSON, 58 summary-полей, 18 ordered outcomes, 19 ссылок |
| Preservation/privacy | Старые bytes, Python sources, schemas, ссылки, известные credentials и private hashes |
| Прежние proofs | Сохранённые packets, policy versions, references, audit/checkpoints |

Новый аудитор выполнил 13253 сравнения: закрытые schemas/profile, полные captured inputs, canonical IDs/digests, bounds, AST selectors, raw/normalized values, точные snippets и CRLF-aware spans, все пары/row states/counts/blockers и публичные wrappers. Проверены все шесть точных fixture derivations, 11 прежних corpus files byte-for-byte и неизменность старого literal extractor. Неразрешимые endpoints дали 2 `ambiguous_source_class`, 2 `duplicate_document_fence`, 1 `source_frozenset_binding_unsupported`, 2 `unsupported_python_literal`, 1 `value_type_mismatch`. Счётчик сравнений не измеряет семантическое качество.

Первый preservation/privacy PASS: 7559 прежних файлов byte-for-byte, 122 прежних tracked Python sources с точными исключениями только `agentwiki/__init__.py` и `agentwiki/__main__.py`, 52 frozen recovery modules, 10 schema literals, 7103 JSON, 119 agentwiki Python AST и 8515 локальных Markdown links. Просканированы 7769 файлов публикации. В них не найдены 598 известных credentials вместе с их hashes и 457 private hashes. Это проверка известных значений и bearer pattern, без утверждения универсального обнаружения секретов.

Сохранённый private recovery workspace предыдущего этапа перепроверен глобальным аудитором; его 503 public copies совпали с прежним примером. Новый workspace не содержит private controls, credentials или DB. Прежние 18 scoped аудитов не повторялись: прежние продуктовые модули и сохранённые публичные результаты остались неизменными, отдельный общий аудит проверил прежние controls. Новые 26 copies проверены новым source-adapter аудитором отдельно.

Proof audit подтвердил 5 full packets, 4 transition bodies, 6 content policy и 8 governance policy versions, 3798 references, 1 audit root и 3 checkpoint links: 2 pending, 1 history_complete. Исторические proofs относятся к прежнему runtime и не становятся одобрением новых semantic reports. После окончательной редакции документов архив и privacy/preservation проверяются повторно.

### 12.3. Границы для совместной работы

Три inputs и профиль читаются последовательно. Стабильность каждого чтения не обеспечивает единого атомарного снимка всех файлов. `created_at` обозначает создание отчёта, а не общий момент версии всех источников. Report закрепляет фактически захваченные тексты; актуальность живого upstream и согласованное состояние нескольких авторских файлов требуют отдельного протокола.

Arg annotations при поиске видимых bindings и неподдержанные generic type parameters разобраны по исходникам до запуска. Восемь случаев не являются систематическим испытанием всех AST-форм или границ ресурсов. Новые tests/suites, аварийные workers, порча отчётов и нагрузочные измерения не добавлялись.

Следующее содержательное исследование — независимо разметить больше связей между реальным исходником и обещаниями документа, учитывать пропущенные обязательства и труд проверки. Расширение грамматики должно отвечать наблюдённому пробелу. Для нескольких агентов сохраняются отдельные роли автора профиля, автора документа и проверяющего; одинаковые результаты двух анализов не создают независимой экспертизы или нового допуска принятия.
