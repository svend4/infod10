# Выполненный пример локально удостоверенного авторства

Время отчёта: 2026-10-16T18:16:13.130914+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Три задания очереди выполнили два зарегистрированных принципала авторов. Первый интегратор объединил
документ и код; второй создал вложенную композицию. Корневой кандидат сохраняет обоих авторов и обоих
интеграторов: четыре принципала и четыре предложения в проверенной цепочке происхождения.

Допуск удостоверяет владение локальным секретом. Он не устанавливает личность человека, идентичность
модели, независимость участников или фактический способ написания текста. Все роли запрограммированы.

## Последовательность

| Состояние | Наблюдение | Ревизия |
|---|---|---:|
| [legacy-before-authorship](stage-01-legacy-before-authorship.json) | A proposal, formal review and editorial opinion exist before authorship activation. | 1 |
| [activated-generation-two](stage-02-activated-generation-two.json) | One-way activation advances policy generation and does not backfill old authorship. | 1 |
| [identity-and-role-enforced](stage-03-identity-and-role-enforced.json) | Claims require a current local grant, its exact principal and its author role. | 1 |
| [authenticated-worker-queue](stage-04-authenticated-worker-queue.json) | Three jobs create immutable grant-bound proofs for two registered author principals. | 1 |
| [exact-scope-and-rollback](stage-05-exact-scope-and-rollback.json) | Out-of-scope submission rolls back the candidate and proof; the original grant can extend its own lease. | 1 |
| [regrant-and-fenced-reclaim](stage-06-regrant-and-fenced-reclaim.json) | A replacement grant cannot continue or relabel an old attempt; a fresh fenced attempt succeeds and old proofs remain inspectable. | 1 |
| [compose-after-author-revocation](stage-07-compose-after-author-revocation.json) | Historical author authorization remains valid when a later integrator composes the exact original components. | 1 |
| [nested-transitive-provenance](stage-08-nested-transitive-provenance.json) | The root pins a four-proposal ancestry and all four contributors, including the intermediate integrator. | 1 |
| [unproven-component-rejected](stage-09-unproven-component-rejected.json) | Composition requires an existing valid proof for every direct and transitive source. | 1 |
| [contributors-separated-globally](stage-10-contributors-separated-globally.json) | New role grants cannot let source authors or intermediate integrators review the root; exclusion covers every write and verdict. | 1 |
| [contributor-coordinator-blocked](stage-11-contributor-coordinator-blocked.json) | A separate coordinator grant for an intermediate integrator remains excluded; an unrelated coordinator still needs formal review and every editorial quorum. | 1 |
| [formal-review-and-first-reviewer](stage-12-formal-review-and-first-reviewer.json) | A valid formal review and one reviewer per write cannot meet the two-principal policy. | 1 |
| [current-approvals-observed](stage-13-current-approvals-observed.json) | Two noncontributor principal IDs approve each write under generation 2; the observation does not itself accept the root. | 1 |
| [accepted-with-provenance-receipt](stage-14-accepted-with-provenance-receipt.json) | Atomic acceptance checks runtime guards, formal review, current local authority, noncontributor quorums and exact authorship; the receipt pins the proof. | 2 |
| [completed-replays-preserve-proof](stage-15-completed-replays-preserve-proof.json) | Current original integrator authority can replay a completed composition despite stale source reads; coordinator replay returns the original receipt without new events. | 2 |
| [historical-proofs-and-runtime-guards](stage-16-historical-proofs-and-runtime-guards.json) | Legacy proposals receive no retroactive proof; a proven and approved competing proposal still cannot override stale_read. | 2 |

## Практический результат

Включение авторства повышает поколение политики с 1 до 2 и не приписывает авторство прежнему предложению.
Новый claim требует действующего допуска автора и точного principal_id. Аренда связывается с конкретным
grant; новый секрет того же принципала не продолжает старую попытку и не переименовывает завершённую.
После fencing reclaim новая попытка получает новый token и новое доказательство авторства.

Исходный допуск автора документа отозван до композиции и принятия. Его историческое доказательство
сохраняется: проверяется действительность допуска в момент записи. Повтор изменяющего API с отозванным
допуском запрещён; чтение публичной истории не требует его восстановления.

Новые reviewer/coordinator grants для автора и промежуточного интегратора не обходят разделение участников.
Автор документа не может голосовать даже по записи кода этой композиции. Запрет относится к approve,
request_changes и abstain, а также к формальному review и новому принятию координатором.

Корневой кандидат получил формальную проверку и по два одобрения от разных непричастных principal_id
для каждой записи. Принятие выполнилось один раз: ревизия 1 → 2. Квитанция schema 2 закрепила точный
authorship_id и digest. Разрешённые повторы вернули прежний результат без новых событий и доказательств.

Прежний кандидат остался без ретроспективного доказательства. Другой удостоверенный и одобренный
документ заблокирован stale_read: полномочия и происхождение не отменяют прежние проверки runtime.

## Публичные материалы

- [Полный отчёт](report.json), [предложения](proposals.json), [авторы очереди](authors.json).
- [Композиции](composition.json), [корневое авторство](root-authorship.json), [все доказательства и аренды](authorship-final.json).
- [Операции и отклонения](operations.json), [решения](decisions.json), [формальные проверки](formal-reviews.json).
- [Полномочия без секретов](grants.json), [политика и квитанции](governance-final.json), [активация и повторная выдача](activation-and-regrant.json).
- [Повторы](replays.json), [заблокированные кандидаты](blocked-candidates.json), [события](events.json).
- [Начальное состояние](initial-state.json), [конечное состояние](final-state.json).

## Границы

SQLite и локальные часы доверенные. Это не внешнее заверение, SSO, распределённая идентичность или
проверка независимости людей и моделей. Голоса и цепочка происхождения не доказывают смысловую
эквивалентность, истинность текста, полноту документации или поведение программы. Предлагаемый код
сохраняется как данные и не исполняется. Публикация Git и выпуск остаются отдельными действиями.

Частные файлы credential имеют права 0600 и находятся в каталоге private/ с правами 0700 вместе с SQLite.
Каталог private/ не входит в публичные артефакты. Публичный числовой token — fencing номер аренды,
а не bearer secret. Секреты и их хеши не публикуются.

Настроено Python workers: 3; заданий авторов: 3; решений: 7; событий runtime: 64.
Размер пула не является измеренной ёмкостью сервиса или числом внешних LLM.
