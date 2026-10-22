# Поэтапная публикация точного рассмотрения плана

Время отчёта: 2026-10-22T18:18:39.690349+02:00 (Europe/Berlin). Технические отметки используют UTC/Unix epoch.

Сначала заново выполнен [сценарий рассмотрения и актуальности](restore-case-retention-review-report.md); его 341 публичный файл сохранён.
Два новых write jobs сохраняют точные intended reviews, затем отдельными обычными step публикуют совместимые review records.

## Этапы

| Этап | Наблюдение |
|---|---|
| [durable-intent-fixes-a-whole-review](restore-case-retention-review-write-stage-01-durable-intent-fixes-a-whole-review.json) | Two ordinary prepare requests fix complete advisory reviews; exact replay recovers a discarded response, status preserves controls, and the old reader refuses the unpublished target. |
| [two-launched-callers-advance-one-fixed-write](restore-case-retention-review-write-stage-02-two-launched-callers-advance-one-fixed-write.json) | Two step processes start before reply collection and return unordered ready/linked states. The old reader refuses the two-link state; a third step completes publication and historical readers become compatible. |
| [prepared-write-completes-without-live-inputs](restore-case-retention-review-write-stage-03-prepared-write-completes-without-live-inputs.json) | With fourteen old live paths absent, a prepared job advances ready/linked/completed from its durable intent. Status recovers a discarded ready reply; old show/record and completed prepare replay remain source-free. Every path returns in finally. |
| [compatible-review-keeps-old-assessment](restore-case-retention-review-write-stage-04-compatible-review-keeps-old-assessment.json) | Completed files are ordinary review records: old show and separate revalidation retain the full original plans. Minimum keeps two unknown-identity holds and long keeps four holds; matching basis authorizes no domain action. |
| [different-requests-do-not-rebind-an-intent](restore-case-retention-review-write-stage-05-different-requests-do-not-rebind-an-intent.json) | Five malformed inputs and five different exact requests refuse without creating unsupported targets or changing either completed job. The immutable operation, actor, verdict, plan and target remain fixed. |
| [publication-preserves-prior-domain-history](restore-case-retention-review-write-stage-06-publication-preserves-prior-domain-history.json) | Both completed statuses, previous reviews, source plans, catalog pages, case details and blocked jobs remain consistent. Source/live history is unchanged, and no domain action, new fault worker or injected partial write occurs. |

## Зафиксированное намерение и текущая фаза

Prepare фиксирует полный intended review: ID, operation, clock, самозаявленные actor/verdict и весь прежний plan. Status показывает фазу, сохраняя result=null до completed.
Три обычных step проходят ready, linked и completed. Старый reader отказывает для незавершённого target и для linked с двумя hard links; после удаления доказанного staging alias он читает обычный review16.
Для minimum два CLI-процесса запущены до сбора первого ответа. Получены ready и linked в неопределённом порядке, затем третий step завершил публикацию. Перекрытие времени процессов и производительность не измерялись.
Для long все 14 прежних live paths временно отсутствуют. Exact prepare replay, status и три шага завершают сохранённое намерение без них. Пути возвращены в finally, старые blocked jobs не перемещались.
Настоящий ответ prepare отброшен и восстановлен точным повтором. Настоящий ответ ready-step отброшен и восстановлен read-only status; повторяющий step мог бы продвинуть следующую фазу, поэтому для восстановления ответа использован status.
После публикации старые show и record replay возвращают ровно intended review. Две отдельные revalidations сохраняют minimum с двумя held и long с четырьмя held; совпавшее основание не разрешает действий.

## Публичные основания

- [Состояния](restore-case-retention-review-write-states.json), [CLI](restore-case-retention-review-write-cli-outcomes.json), [отказы](restore-case-retention-review-write-rejections.json).
- [Восстановление ответов](restore-case-retention-review-write-reply-recovery.json), [два запущенных caller](restore-case-retention-review-write-launched-callers.json), [совместимость review](restore-case-retention-review-write-review-compatibility.json).
- [Controls](restore-case-retention-review-write-control-observations.json), [текущее основание](restore-case-retention-review-write-current-basis-observations.json).
- [Источник](restore-case-retention-review-write-source-observations.json), [живая среда](restore-case-retention-review-write-live-observations.json), [машинный отчёт](restore-case-retention-review-write-report.json).

## Границы результата

Шесть этапов, 58 новых обычных CLI: 41 успешный и 17 ожидаемых отказов. С повторённым основанием это 371 CLI и 14 прежних interrupt workers, всего 385 различных дочерних процессов.
Сохранены два новых write jobs и два новых review records; прежние два reviews, plans, catalogs, cases, blocked jobs и история R4/36 и R5/59 сохранены.
Демонстратор не читает private control bodies или staging contents. Публикуются безопасные API/Store ответы, счётчики и булевы metadata-наблюдения.
Новых fault workers, SIGKILL, injected partial writes и отказов хранения нет. Восстановление partial staging и неподдержанное окно до durable intent этим обычным сценарием не воспроизводились.
Самозаявленные actor/verdict и completed не удостоверяют личность, независимость рассмотрения, смысловую истинность документации либо полномочия архивации, удаления и доменных записей.
