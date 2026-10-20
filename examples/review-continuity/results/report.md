# Выполненный пример сохранения редакторских решений

Время отчёта: 2026-10-20T11:20:29.988614+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Программируемые локальные авторы предложили вымышленный документ и литеральную Python-конфигурацию.
Интегратор создал один общий кандидат A. Каталог также содержит несвязанный раздел D.
Проверяются целые значения JSON и SAMPLE_LIMIT с точной цитатой объявленного обещания; код не исполняется.

## Последовательность

| Состояние | Наблюдение | Ревизия |
|---|---|---:|
| [authenticated-candidate](stage-01-authenticated-candidate.json) | Three author jobs and one composition retain local provenance; the code is stored data. | 1 |
| [preactivation-opinion](stage-02-preactivation-opinion.json) | An ordinary content-pinned opinion is saved before continuity; it receives no future stamp retroactively. | 1 |
| [continuity-activated](stage-03-continuity-activated.json) | Explicit activation advances governance and requires fresh decisions; schema-one downgrade is refused. | 1 |
| [exact-current-approvals](stage-04-exact-current-approvals.json) | Two local reviewers pin each write and its local basis; a wrong packet or absent grant is rejected. | 1 |
| [unrelated-catalog-keeps-votes](stage-05-unrelated-catalog-keeps-votes.json) | D changes the full packet, but A's local basis and four original approvals remain current without new votes. | 1 |
| [current-objection-blocks](stage-06-current-objection-blocks.json) | A third current reviewer records request_changes; the existing quorum cannot bypass the veto. | 1 |
| [objection-survives-unrelated-edit](stage-07-objection-survives-unrelated-edit.json) | The same continuity rule carries request_changes and approvals through another unrelated D edit. | 1 |
| [objection-explicitly-replaced](stage-08-objection-explicitly-replaced.json) | C explicitly supersedes the objection with abstain; A/B approvals are still the original four decisions. | 1 |
| [revoked-grant-stops-votes](stage-09-revoked-grant-stops-votes.json) | Continuity does not override a grant revocation; the original B votes stop counting. | 1 |
| [reissued-grant-does-not-restore](stage-10-reissued-grant-does-not-restore.json) | A new B grant has the same principal but cannot reactivate decisions tied to the revoked grant. | 1 |
| [fresh-grant-decisions](stage-11-fresh-grant-decisions.json) | B writes two new CAS-linked opinions with the current grant; A's original approvals remain usable. | 1 |
| [selected-change-and-return](stage-12-selected-change-and-return.json) | A changed and returned to its old local basis; every intermediate edge is checked, so the old opinions do not revive. | 1 |
| [fresh-after-selected-change](stage-13-fresh-after-selected-change.json) | Fresh opinions supersede A/B heads after the selected-context barrier; old objects remain historical. | 1 |
| [direct-governance-reset](stage-14-direct-governance-reset.json) | An identical direct governance policy creates a generation gap without a catalog transition; fresh review is required. | 1 |
| [fresh-after-direct-reset](stage-15-fresh-after-direct-reset.json) | The current A/B grants explicitly approve the current exact packet after the governance reset. | 1 |
| [objection-before-long-history](stage-16-objection-before-long-history.json) | C records a fresh objection at the same generation as the current quorum, before the bounded catalog history grows. | 1 |
| [bounded-history-requires-consideration](stage-17-bounded-history-requires-consideration.json) | After 65 unrelated updates, old current heads exceed the 64-edge reader and require explicit fresh consideration. | 1 |
| [fresh-quorum-keeps-old-objection-blocker](stage-18-fresh-quorum-keeps-old-objection-blocker.json) | A/B supply a fresh quorum, but C's unresolved current-head objection remains a proposal-level blocker. | 1 |
| [objection-explicitly-reconsidered](stage-19-objection-explicitly-reconsidered.json) | C explicitly replaces the old head with a current abstain; the fresh A/B quorum can now be considered. | 1 |
| [accepted-with-preserved-quorum](stage-20-accepted-with-preserved-quorum.json) | One final unrelated D transition preserves the same four A/B decisions; receipt schema 4 pins their old/current packets and transition proofs. | 2 |
| [historical-replays-preserve-objects](stage-21-historical-replays-preserve-objects.json) | Current authorized replays return old objects, including an unstamped preactivation decision, without new votes or acceptance events. | 2 |

## Что произошло

Явное включение continuity создало новое поколение governance. Прежнее решение осталось без нового
штампа и не было переоформлено задним числом. Новые решения schema 3 закрепили полный пакет и локальное основание.

Изменение D поменяло полный пакет и поколения, но сохранило локальное основание A и те же четыре одобрения.
Каждый промежуточный переход проверялся отдельно. Возражение C также сохранилось после следующей правки D
и блокировало принятие до явной замены на abstain. Число разных ID не устанавливает независимость людей.

Отзыв B остановил его голоса; новый grant того же principal не оживил прежние решения. Потребовались новые
CAS-связанные мнения B. Изменение выбранного A с последующим возвратом прежнего основания также потребовало
свежих решений. Прямая запись идентичной governance-политики создала отдельный барьер для старых голосов.

Затем выполнены 65 несвязанных обновлений каталога. Путь старых текущих голов превысил предел 64 перехода.
Свежие одобрения A/B обеспечили кворум, но старое возражение C не исчезло: continuity_reconsideration_required
сохранился как блокер всего предложения. Только явное свежее abstain C устранило этот блокер.

Последняя несвязанная правка D сохранила четыре уже существующих одобрения. Принятие прошло один раз:
ревизия 1 → 2. Квитанция schema 4 закрепила использованные decision IDs, старые и текущий пакеты и
хешированную цепочку перехода. Хеш цепочки не является внешней подписью или доказательством личности.

Исторические повторы вернули прежние объекты, включая решение до активации, без новых голосов и событий
принятия. Сохранённый исходный пакет прочитан после принятия без изменения тела.

## Публичные материалы

- [Полный отчёт](report.json), [активация](activation.json), [итоговая политика](policy.json).
- [Авторы](authors.json), [композиция](composition.json), [предложения](proposals.json), [формальная проверка](formal-review.json).
- [Пакеты](packets.json), [наблюдения](observations.json), [решения](decisions.json), [операции](operations.json).
- [Обновления каталога](policy-updates.json), [ограниченная последовательность из 65 обновлений](history-sweep.json).
- [Принятие и квитанция](acceptance.json), [повторы](replays.json), [события](events.json).
- [История continuity](continuity-final.json), [governance](governance-final.json), [content](content-final.json), [происхождение](authorship-final.json).
- [Начальное состояние](initial-state.json), [конечное состояние](final-state.json), [допуски без секретов](grants.json).

## Границы

Проверяется непрерывность объявленных оснований и локальных допусков. Истинность свободного текста, полнота
описи, поведение программы, человеческое понимание и независимость людей или моделей не установлены.
Внешние LLM и человеческие решения отсутствуют; предложенный код сохраняется как данные и не исполняется.
SQLite, часы и административная политика доверенные. Данные read/query по-прежнему сравниваются по digest;
новое правило не обнаруживает все промежуточные изменения восстановленных байтов данных.

История ограничена 64 переходами на решение. Непроверенная действующая голова требует свежего рассмотрения
либо отзыва допуска; один свежий кворум не устраняет незавершённое старое возражение. Последовательность
из 65 обновлений показывает этот договор, без измерения производительности или ёмкости портала.

private/ имеет права 0700; .credential — 0600. Секреты, их хеши и SQLite не публикуются. Отдельные снимки
JSON последовательны; новая приёмка выполняется в Store.accept. Git и выпуск остаются отдельными действиями.

Настроено Python workers: 3; авторских заданий: 3; решений: 23; обновлений каталога: 70; событий: 277.
Размер пула и этот сценарий не являются измеренной ёмкостью внешних агентов.
