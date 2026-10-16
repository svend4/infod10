# Выполненный пример обязательного рассмотрения содержания

Время отчёта: 2026-10-16T18:40:31.047927+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Программируемые локальные авторы предложили документ с явным JSON-контрактом и Python-файлы.
Интегратор составил шесть кандидатов. Политика требует точный комплект документа и кода, совпадение
целого значения limit и SAMPLE_LIMIT, а также уникальную дословную цитату заявленного обещания.

Документ и код являются вымышленными данными. AST разбирается без исполнения. Локальные идентификаторы
и секреты допуска не устанавливают личность человека, независимость моделей или фактическое понимание текста.

## Последовательность

| Состояние | Наблюдение | Ревизия |
|---|---|---:|
| [authenticated-candidates](stage-01-authenticated-candidates.json) | Seven author jobs and six exact compositions carry local authorship; none has been accepted. | 1 |
| [generic-opinions-before-content](stage-02-generic-opinions-before-content.json) | The former editorial API can record opinions without inspecting the declared cross-file content. | 1 |
| [content-activation](stage-03-content-activation.json) | Content policy generation 1 advances governance to 3; generic old opinions no longer meet the current gate. | 1 |
| [formal-review-is-separate](stage-04-formal-review-is-separate.json) | All seven inspected candidates pass formal runtime review; this does not establish declared content consistency. | 1 |
| [exact-pin-and-contributor-gate](stage-05-exact-pin-and-contributor-gate.json) | Every new decision must identify its exact content packet; a new reviewer grant does not separate its author principal. | 1 |
| [mismatch-blocked](stage-06-mismatch-blocked.json) | Document limit 4 and code limit 5 disagree despite the old generic approvals. | 1 |
| [unresolved-blocked](stage-07-unresolved-blocked.json) | A computed Python expression remains unresolved; the workflow never executes it. | 1 |
| [missing-quote-blocked](stage-08-missing-quote-blocked.json) | A matching numeric relation does not repair the absent exact promise quotation. | 1 |
| [ambiguous-quote-blocked](stage-09-ambiguous-quote-blocked.json) | Two occurrences of the exact promise quotation make its anchor ambiguous. | 1 |
| [whole-bundle-required](stage-10-whole-bundle-required.json) | The bounded policy requires exactly its two configured write IDs; partial and extra bundles are blocked. | 1 |
| [eligible-before-policy-refresh](stage-11-eligible-before-policy-refresh.json) | Two noncontributor reviewers pin the exact packet for each write; decisions themselves do not alter that packet. | 1 |
| [policy-refresh-invalidates-opinions](stage-12-policy-refresh-invalidates-opinions.json) | An identical policy body creates content generation 2 and governance generation 4; earlier pins cannot vote for the new packet. | 1 |
| [historical-decision-replay](stage-13-historical-decision-replay.json) | A current original grant can replay the exact historical operation; its old pin remains historical and a new operation rejects it. | 1 |
| [fresh-consideration-under-current-policy](stage-14-fresh-consideration-under-current-policy.json) | Both registered reviewers reconsider each write and pin the new packet; formal review, provenance and runtime guards remain separate requirements. | 1 |
| [accepted-with-content-receipt](stage-15-accepted-with-content-receipt.json) | New acceptance checks the exact packet in its transaction, advances revision 1 to 2, and retains the packet referenced by receipt schema 3. | 2 |
| [historical-acceptance-is-stable](stage-16-historical-acceptance-is-stable.json) | The accepted candidate now has stale source reads; authorized replay returns its stored receipt before new live packet checks and creates no new event. | 2 |

## Практический результат

До включения нового режима общие одобрения можно было записать для совпадающего и несовпадающего кода.
Включение политики содержания повышает поколение governance с 2 до 3. Прежние голоса остаются историей
и не разрешают новое принятие. Новое решение должно закрепить точный digest вычисленного пакета.

Различие 4 и 5, вычисляемое выражение, отсутствующая либо неоднозначная цитата, неполный либо расширенный
комплект блокируют принятие. Формальная проверка синтаксиса при этом проходит: эти проверки имеют разные основания.
Авторы и интегратор исключены из согласования собственного кандидата, даже с другим reviewer grant.

Два непричастных principal_id явно одобрили каждый файл по пакету поколения 3. Повторная запись такой же
политики создала поколение содержания 2 и governance 4. Ранее согласованный пакет стал историческим;
повтор старой операции возвращает старый голос, но не переносит его на новый пакет. Потребовались новые решения.

Принятие выполнилось один раз: ревизия 1 → 2. Квитанция schema 3 закрепила происхождение и точный пакет
содержания. После изменения базы текущий просмотр имеет устаревшие исходные чтения; разрешённый повтор
принятия возвращает прежнюю квитанцию без нового события и без повторного разрешения публикации.

Запись формального review и редакторских решений не меняет digest пакета. Политика и зависимости кандидата
входят в его основание; число событий и несвязанные метаданные не используются как замена этому основанию.

## Публичные материалы

- [Полный отчёт](report.json), [политика](policy.json), [активация и обновление](activation.json).
- [Авторы](authors.json), [композиции](composites.json), [все предложения](proposals.json).
- [Пакеты по вариантам](packets.json), [текущий и сохранённые пакеты принятого кандидата](root-content-final.json).
- [Решения](decisions.json), [формальные проверки](formal-reviews.json), [отклонённые варианты](blocked-candidates.json).
- [Операции и отказы](operations.json), [принятие с квитанцией](acceptance.json), [повторы](replays.json).
- [Состояние политики содержания](content-final.json), [governance](governance-final.json), [происхождение](authorship-final.json).
- [Допуски без секретов](grants.json), [события](events.json), [начальное](initial-state.json) и [конечное](final-state.json) состояние.

## Границы

Это обязательная проверка явно объявленного контракта и привязка рассмотрения к точному снимку. Она не
распознаёт все требования естественного языка, не доказывает полноту описи, истинность прозы, поведение
программы, независимость участников или то, что человек прочёл документ. Все решения запрограммированы.
Доверены владелец SQLite, политика и часы; внешнего заверения, SSO и вызовов LLM нет.

private/ имеет права 0700; секреты .credential имеют права 0600. Секреты, их хеши и SQLite не входят
в публичные материалы. Публикация Git и выпуск являются отдельными действиями.

Настроено Python workers: 3; заданий авторов: 7; композиций: 6; решений: 16; событий runtime: 99.
Размер пула и этот ограниченный сценарий не измеряют ёмкость сервиса или число поддерживаемых LLM.
