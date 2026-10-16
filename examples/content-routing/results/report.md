# Выполненный пример маршрутизации рассмотрения содержания

Время отчёта: 2026-10-16T19:01:40.599621+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Программируемые локальные авторы предложили вымышленные документы с JSON-контрактом и Python-файлы
с целой константой SAMPLE_LIMIT. Предложенный код разбирается через AST и не исполняется.

Каталог содержит четыре раздела: A связывает doc A и общий код; B — doc B и тот же общий код;
C — doc B и код C; D — отдельную пару документа и кода. Идентификаторы sample-limit и
sample-promise намеренно повторяются внутри разных разделов и имеют локальную область.

## Последовательность

| Состояние | Наблюдение | Ревизия |
| --- | --- | --- |
| [authenticated-candidates](stage-01-authenticated-candidates.json) | Eight author jobs and five compositions retain local provenance; all share runtime revision 1. | 1 |
| [schema-one-policy-enabled](stage-02-schema-one-policy-enabled.json) | The existing schema-one content gate covers exactly D; its packet keeps the original schema and rules. | 1 |
| [schema-one-packet-pinned](stage-03-schema-one-packet-pinned.json) | Two registered reviewers each pin both D writes to the exact stored schema-one packet; D is not accepted yet. | 1 |
| [section-catalog-upgrade](stage-04-section-catalog-upgrade.json) | A/B/C form one overlap closure; D is separate. Repeated local promise and obligation IDs remain inside section namespaces. | 1 |
| [historical-schema-one-replay](stage-05-historical-schema-one-replay.json) | Historical schema-one packet bytes and decision pins survive the schema-two upgrade; replay returns the old operation. | 1 |
| [formal-review-and-exact-pins](stage-06-formal-review-and-exact-pins.json) | Every inspected candidate passes formal runtime review. Missing and wrong packet pins still reject new content decisions. | 1 |
| [transitive-routing-closure](stage-07-transitive-routing-closure.json) | Writing doc A initially selects only A. Shared code adds B, then shared doc B adds C; missing writes block the complete closure. | 1 |
| [locally-complete-section-is-incomplete](stage-08-locally-complete-section-is-incomplete.json) | A has both of its writes, but its connected B/C requirements also require doc B and code C. | 1 |
| [one-passing-section-is-insufficient](stage-09-one-passing-section-is-insufficient.json) | A matches at 4 while doc B declares 5 against code values 4. B and C fail under the same atomic candidate. | 1 |
| [uncovered-writes-remain-blocked](stage-10-uncovered-writes-remain-blocked.json) | A write outside the catalog blocks both an outside-only proposal and an otherwise complete D proposal. | 1 |
| [new-policy-requires-fresh-votes](stage-11-new-policy-requires-fresh-votes.json) | The catalog upgrade changes global policy generation; old D opinions remain historical and cannot authorize its new packet. | 1 |
| [independent-candidates-approved](stage-12-independent-candidates-approved.json) | Both reviewers pin every write of D and A/B/C before either candidate is accepted; opinions themselves do not change packet identity. | 1 |
| [disjoint-acceptance-preserves-other-packet](stage-13-disjoint-acceptance-preserves-other-packet.json) | D advances runtime revision to 2. A/B/C read/query bases, exact packet and existing votes remain current without another decision. | 2 |
| [overlap-accepted-from-earlier-revision](stage-14-overlap-accepted-from-earlier-revision.json) | The A/B/C candidate captured at revision 1 accepts at revision 3 using its unchanged packet and the votes recorded before D acceptance. | 3 |
| [accepted-replays-preserve-history](stage-15-accepted-replays-preserve-history.json) | Authorized replays return both stored receipts without new events. Old schema-one and accepted schema-two packet bodies remain exact history. | 3 |
| [catalog-validation-preserves-active-policy](stage-16-catalog-validation-preserves-active-policy.json) | Duplicate section IDs and an empty catalog are rejected; failed administration does not advance either policy generation. | 3 |

## Что произошло

1. Восемь авторских заданий и пять композиций созданы на ревизии 1 с аутентифицированным локальным происхождением.
2. Прежняя политика schema 1 потребовала точную пару D. Четыре решения закрепили её пакет, но принятия ещё не было.
3. Переход на каталог schema 2 повысил поколение содержания до 2, governance — до 4. Исторический пакет schema 1
   сохранился побайтно в каноническом UTF-8 JSON. Повтор прежней операции вернул старый голос, который не стал действующим.
4. Запись только doc A сначала выбирает A, затем общий код приводит B, а общий doc B приводит C. Полная связная группа
   требует четыре файла. Даже локально полная пара A недостаточна; непокрытые записи блокируют весь кандидат.
5. В варианте несовпадения A проходит, B и C не проходят. Один успешный раздел не предоставляет разрешения общей правке.
6. Два непричастных проверяющих закрепили каждый файл D и A/B/C до первого принятия. D принято на ревизии 2.
   Пакет A/B/C, его основания и действующие голоса остались теми же. Повторные решения не потребовались.
7. Кандидат A/B/C с исходными чтениями ревизии 1 принят на ревизии 3. Обе квитанции schema 3 закрепили пакеты schema 2.
   Разрешённые повторы вернули прежние квитанции без новых событий. Исторический пакет schema 1 остался неизменным.

Глобальное поколение политики по-прежнему консервативно обновляет необходимость рассмотрения всего каталога.
Локальность разделов относится к выбору обязательств и фактическим read/query основаниям, а не к отмене проверки политики.

## Публичные материалы

- [Полный отчёт](report.json), [каталог schema 2](policy.json), [прежняя политика schema 1](policy-schema-one.json).
- [Активация и переход](activation.json), [маршруты](routing.json), [пакеты вариантов](packets.json).
- [Исторические пакеты](historical-packets.json), [наблюдения до и после принятия D](observations.json).
- [Авторы](authors.json), [композиции](composites.json), [все предложения](proposals.json), [происхождение](authorship-final.json).
- [Решения](decisions.json), [формальные проверки](formal-reviews.json), [операции и отказы](operations.json).
- [Принятие A/B/C](acceptance.json), [принятие D](disjoint-acceptance.json), [повторы](replays.json).
- [Текущие и сохранённые пакеты A/B/C](root-content-final.json), [то же для D](disjoint-content-final.json).
- [Политика содержания](content-final.json), [governance](governance-final.json), [допуски без секретов](grants.json).
- [События](events.json), [начальное](initial-state.json) и [конечное](final-state.json) состояние.

## Границы

Каталог и опись задаёт администратор. Маршрутизация не обнаруживает все неявные требования естественного языка
и не доказывает полноту политики, смысл всей прозы, её истинность или поведение программы. Все решения запрограммированы.
Локальные идентификаторы и допуски не устанавливают личность человека, независимость моделей или понимание текста.
Доверены владелец SQLite, приложение и часы; внешнего заверения, SSO и вызовов LLM нет.

private/ имеет права 0700; секреты .credential имеют права 0600. Секреты, их хеши и SQLite не входят в публичные материалы.
Этот сценарий не создаёт выпуск и не публикует Git. Просмотр пакета совещателен; новое принятие разрешает только транзакция Store.accept.

Настроено Python workers: 3; заданий авторов: 8; композиций: 5; решений: 16; событий runtime: 97.
Этапов: 16; ожидаемых отказов API: 6; заблокированных принятий: 6.
Размер пула и этот ограниченный сценарий не измеряют ёмкость сервиса или число поддерживаемых LLM.
