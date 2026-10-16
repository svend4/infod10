# Выполненный пример локальных полномочий и обязательного принятия

Время отчёта: 2026-10-16T17:28:38.616444+02:00 (Europe/Berlin). Технические отметки политики и решений используют UTC.

Два программных автора подготовили три вклада; интегратор объединил документ и код в один кандидат.
Политика требует двух разных действующих принципалов для каждой записи файла и отдельного координатора.
Имена авторов остаются самозаявленными. Полномочия редакторов и координатора определяются владением
локальным секретом с ограниченной областью; люди, модели и независимость экспертизы не удостоверяются.

## Последовательность

| Состояние | Наблюдение | Ревизия |
|---|---|---:|
| [no-editorial-approvals](stage-01-no-editorial-approvals.json) | A formal review and management credential do not satisfy the per-write editorial quorum. | 1 |
| [one-principal-two-credentials](stage-02-one-principal-two-credentials.json) | Two credentials for the same principal count as one reviewer per write. | 1 |
| [code-missing-second-reviewer](stage-03-code-missing-second-reviewer.json) | The document has two reviewers; code still lacks its own second approval. | 1 |
| [all-writes-approved](stage-04-all-writes-approved.json) | Every write has two distinct active principals; this is an observation, not acceptance. | 1 |
| [request-changes-veto](stage-05-request-changes-veto.json) | The latest request_changes replaces the earlier approval and blocks acceptance. | 1 |
| [veto-explicitly-replaced](stage-06-veto-explicitly-replaced.json) | Only a replacement naming the current decision clears this principal's veto. | 1 |
| [reviewer-revoked](stage-07-reviewer-revoked.json) | Revocation invalidates this grant's former approvals before acceptance. | 1 |
| [regrant-does-not-resurrect-decisions](stage-08-regrant-does-not-resurrect-decisions.json) | A new credential for the same principal does not reactivate old grant-bound decisions. | 1 |
| [fresh-regranted-decisions](stage-09-fresh-regranted-decisions.json) | New decisions explicitly pin the new grant and the current policy generation. | 1 |
| [policy-generation-changed](stage-10-policy-generation-changed.json) | A new policy generation invalidates old-generation decisions even when its fields are equal. | 1 |
| [fresh-current-generation](stage-11-fresh-current-generation.json) | Both reviewers recorded new decisions against policy generation 2. | 1 |
| [accepted](stage-12-accepted.json) | Authorization, quorum, latest decisions and old runtime guards are checked inside acceptance. | 2 |
| [authorized-historical-receipt](stage-13-authorized-historical-receipt.json) | A revoked coordinator cannot replay; a newly authorized one receives the original accepted revision. | 2 |
| [old-runtime-guards-preserved](stage-14-old-runtime-guards-preserved.json) | Fresh approvals cannot override the competing candidate's stale document read. | 2 |

## Практический результат

Один принципал с двумя секретами не создаёт два голоса. Одобрение документа не покрывает код.
`request_changes` заменяет прежний голос; устаревшая попытка замены отклоняется по CAS.
Отзыв полномочия лишает старое решение силы, а новое полномочие того же принципала требует нового решения.
После смены поколения политики нужны новые решения для каждого файла. Решение привязано к точному
предложению, его digest, поколению политики и выдавшему полномочие grant.

Основной кандидат принят один раз: ревизия 1 → 2. Разрешённый повтор возвращает прежнюю квитанцию;
повтор с отозванным полномочием координатора отклоняется. Конкурирующий документ получил новые
одобрения, но остался заблокирован существующей проверкой `stale_read`.

## Публичные материалы

- [Полный отчёт](report.json), [предложения](proposals.json), [композиция](composition.json).
- [Операции и отклонения](operations.json), [редакторские решения](decisions.json), [формальные проверки](formal-reviews.json).
- [Полномочия без секретов](grants.json), [состояние политики](governance-final.json), [смена политики и отзывы](policy-transitions.json).
- [Начальное состояние](initial-state.json), [конечное состояние](final-state.json), [события](events.json).
- [Состояние конкурирующего предложения](competing-final-governance.json), [авторы](authors.json).

## Границы

Это локальный прототип с доверенным владельцем SQLite. Он не реализует SSO, распределённую идентичность,
проверку независимости участников или неизменяемое внешнее свидетельство. Голосование не устанавливает
истинность текста и не подключает advisory-отчёты семантики к принятой политике.

Секреты записаны отдельно с правами 0600 в каталог `private/` с правами 0700 до выдачи полномочий.
Этот каталог, база SQLite и её служебные файлы не входят в публичные материалы.
Публикация Git и runtime-принятие остаются отдельными действиями; release не создавался.

Пул Python workers: 3; решений: 16; событий runtime: 52.
Размер пула не является измеренной ёмкостью сервиса или числом LLM.
