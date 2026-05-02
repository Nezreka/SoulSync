import { useForm } from '@tanstack/react-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';

import {
  Button,
  FormActions,
  FormError,
  FormField,
  OptionButton,
  OptionButtonGroup,
  OptionCard,
  OptionCardGroup,
  TextArea,
  TextInput,
} from '@/components/form';
import { getShellProfileContext } from '@/platform/shell/bridge';
import { useShellBridge } from '@/platform/shell/route-controllers';

import type { IssuePriority, IssueReportPayload } from '../-issues.types';

import { createIssue, issueCountsQueryOptions } from '../-issues.api';
import {
  REFRESH_EVENT,
  createDefaultIssueTitle,
  getIssueCategoriesForEntity,
} from '../-issues.helpers';
import styles from './issue-detail-modal.module.css';

const ISSUE_DOMAIN_QUERY_KEY = ['issues'] as const;

interface ReportIssueFormValues {
  category: string;
  description: string;
  priority: IssuePriority;
  title: string;
}

const DEFAULT_REPORT_ISSUE_VALUES: ReportIssueFormValues = {
  category: '',
  description: '',
  priority: 'normal',
  title: '',
};

export function IssueDomainHost() {
  const bridge = useShellBridge();
  const queryClient = useQueryClient();
  const profile = getShellProfileContext(bridge);
  const [reportPayload, setReportPayload] = useState<IssueReportPayload | null>(null);
  const profileId = profile?.profileId ?? 0;

  const countsQuery = useQuery({
    ...issueCountsQueryOptions(profileId),
    enabled: profileId > 0,
  });

  useEffect(() => {
    if (countsQuery.data) {
      updateBadge(countsQuery.data.open || 0);
    }
  }, [countsQuery.data]);

  useEffect(() => {
    const handleRefresh = () => {
      void queryClient.invalidateQueries({ queryKey: ISSUE_DOMAIN_QUERY_KEY });
    };

    window.addEventListener(REFRESH_EVENT, handleRefresh);
    return () => {
      window.removeEventListener(REFRESH_EVENT, handleRefresh);
    };
  }, [queryClient]);

  useEffect(() => {
    window.SoulSyncIssueDomain = {
      openReportIssue(payload) {
        setReportPayload(payload);
      },
      closeReportIssue() {
        setReportPayload(null);
      },
      refresh() {
        void queryClient.invalidateQueries({ queryKey: ISSUE_DOMAIN_QUERY_KEY });
      },
    };

    return () => {
      if (window.SoulSyncIssueDomain?.openReportIssue) {
        window.SoulSyncIssueDomain = undefined;
      }
    };
  }, [queryClient]);

  if (!reportPayload) return null;

  return createPortal(
    <ReportIssueModal
      key={`${reportPayload.entityType}:${reportPayload.entityId}`}
      payload={reportPayload}
      profileId={profileId}
      onClose={() => setReportPayload(null)}
      onSubmitted={() => {
        setReportPayload(null);
        void queryClient.invalidateQueries({ queryKey: ISSUE_DOMAIN_QUERY_KEY });
      }}
    />,
    document.body,
  );
}

function ReportIssueModal({
  onClose,
  onSubmitted,
  payload,
  profileId,
}: {
  onClose: () => void;
  onSubmitted: () => void;
  payload: IssueReportPayload;
  profileId: number;
}) {
  const categories = useMemo(
    () => getIssueCategoriesForEntity(payload.entityType),
    [payload.entityType],
  );
  const entityLabel =
    payload.entityType === 'track' ? 'Track' : payload.entityType === 'album' ? 'Album' : 'Artist';

  const createMutation = useMutation({
    mutationFn: async (values: ReportIssueFormValues) => {
      await createIssue(profileId, {
        entity_type: payload.entityType,
        entity_id: String(payload.entityId),
        category: values.category,
        title: values.title,
        description: values.description,
        priority: values.priority,
      });
    },
    onSuccess: () => {
      notify('Issue reported successfully', 'success');
      onSubmitted();
    },
  });

  const form = useForm({
    defaultValues: DEFAULT_REPORT_ISSUE_VALUES,
    validators: {
      onSubmit: ({ value }) => validateReportIssueForm(profileId, value),
    },
    onSubmit: async ({ value, formApi }) => {
      const normalizedValues = normalizeReportIssueFormValues(value);

      createMutation.reset();
      formApi.setErrorMap({ onSubmit: undefined });

      try {
        await createMutation.mutateAsync(normalizedValues);
      } catch (mutationError) {
        const message =
          mutationError instanceof Error ? mutationError.message : 'Failed to submit issue';
        formApi.setErrorMap({ onSubmit: message });
        notify(message, 'error');
        throw mutationError;
      }
    },
  });

  return (
    <div
      className={styles.modalOverlay}
      role="dialog"
      aria-modal="true"
      aria-labelledby="report-issue-title"
      onClick={onClose}
    >
      <form
        className={`${styles.modal} ${styles.reportIssueModal}`}
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void form.handleSubmit().catch(() => undefined);
        }}
      >
        <div className={styles.modalHeader}>
          <h3 className={styles.modalHeaderTitle} id="report-issue-title">
            Report Issue - {entityLabel}
          </h3>
          <button
            className={styles.modalClose}
            type="button"
            onClick={onClose}
            aria-label="Close report issue modal"
          >
            &times;
          </button>
        </div>

        <div className={styles.modalBody}>
          <div className={styles.reportIssueEntityInfo}>
            <div className={styles.reportIssueEntityName}>{payload.entityName}</div>
            {payload.artistName ? (
              <div className={styles.reportIssueEntityArtist}>
                {payload.artistName}
                {payload.albumTitle ? ` - ${payload.albumTitle}` : ''}
              </div>
            ) : null}
          </div>

          <FormField
            label="What's the problem?"
            helperText="Pick the closest match for the report."
          >
            <form.Field name="category">
              {(field) => (
                <OptionCardGroup>
                  {categories.map(([category, meta]) => (
                    <OptionCard
                      key={category}
                      description={meta.description}
                      icon={meta.icon}
                      onClick={() => {
                        field.handleChange(category);
                        createMutation.reset();
                        form.setErrorMap({ onSubmit: undefined });
                        if (!form.getFieldMeta('title')?.isTouched) {
                          form.setFieldValue(
                            'title',
                            createDefaultIssueTitle(category, payload.entityName),
                            { dontUpdateMeta: true },
                          );
                        }
                      }}
                      selected={field.state.value === category}
                      title={meta.label}
                    />
                  ))}
                </OptionCardGroup>
              )}
            </form.Field>
          </FormField>

          <form.Subscribe selector={(state) => state.values.category}>
            {(selectedCategory) =>
              selectedCategory ? (
                <>
                  <form.Field name="title">
                    {(field) => (
                      <FormField
                        helperText="A short summary that makes the problem obvious."
                        htmlFor="report-issue-title-input"
                        label="Title"
                      >
                        <TextInput
                          id="report-issue-title-input"
                          maxLength={200}
                          onBlur={field.handleBlur}
                          onChange={(event) => {
                            field.handleChange(event.target.value);
                            createMutation.reset();
                            form.setErrorMap({ onSubmit: undefined });
                          }}
                          placeholder="Brief summary of the issue..."
                          value={field.state.value}
                        />
                      </FormField>
                    )}
                  </form.Field>

                  <form.Field name="description">
                    {(field) => (
                      <FormField
                        helperText="Include any details that will help triage the issue."
                        htmlFor="report-issue-desc-input"
                        label="Details"
                      >
                        <TextArea
                          id="report-issue-desc-input"
                          maxLength={2000}
                          onBlur={field.handleBlur}
                          onChange={(event) => field.handleChange(event.target.value)}
                          placeholder="Provide more details about what's wrong..."
                          rows={4}
                          value={field.state.value}
                        />
                      </FormField>
                    )}
                  </form.Field>

                  <form.Field name="priority">
                    {(field) => (
                      <FormField
                        helperText="Set the urgency if this needs faster attention."
                        label="Priority"
                      >
                        <OptionButtonGroup>
                          {(['low', 'normal', 'high'] as const).map((priority) => (
                            <OptionButton
                              key={priority}
                              onClick={() => field.handleChange(priority)}
                              selected={field.state.value === priority}
                            >
                              {priority[0].toUpperCase()}
                              {priority.slice(1)}
                            </OptionButton>
                          ))}
                        </OptionButtonGroup>
                      </FormField>
                    )}
                  </form.Field>
                </>
              ) : null
            }
          </form.Subscribe>

          <form.Subscribe selector={(state) => state.errors}>
            {(errors) => {
              const error = getReportIssueFormError(errors);
              return <FormError message={error} />;
            }}
          </form.Subscribe>
        </div>

        <FormActions className={styles.modalFooter}>
          <Button className={styles.modalButtonSecondary} type="button" onClick={onClose}>
            Cancel
          </Button>
          <form.Subscribe
            selector={(state) => ({
              category: state.values.category,
              isSubmitting: state.isSubmitting,
              title: state.values.title,
            })}
          >
            {(state) => {
              const isSubmitting = state.isSubmitting || createMutation.isPending;
              return (
                <Button
                  className={styles.modalButtonPrimary}
                  type="submit"
                  disabled={!state.category || !state.title.trim() || isSubmitting}
                >
                  {isSubmitting ? 'Submitting...' : 'Submit Issue'}
                </Button>
              );
            }}
          </form.Subscribe>
        </FormActions>
      </form>
    </div>
  );
}

function normalizeReportIssueFormValues(values: ReportIssueFormValues): ReportIssueFormValues {
  return {
    category: values.category,
    description: values.description.trim(),
    priority: values.priority,
    title: values.title.trim(),
  };
}

function validateReportIssueForm(
  profileId: number,
  values: ReportIssueFormValues,
): string | undefined {
  const normalizedValues = normalizeReportIssueFormValues(values);
  if (!profileId) return 'Profile is still loading';
  if (!normalizedValues.category) return 'Please select an issue category';
  if (!normalizedValues.title) return 'Please provide a title for the issue';
  return undefined;
}

function getReportIssueFormError(errors: Array<unknown>): string {
  const error = errors.find(Boolean);
  if (!error) return '';
  if (typeof error === 'string') return error;
  if (error instanceof Error) return error.message;
  return String(error);
}

function notify(message: string, type: 'success' | 'error' | 'warning' | 'info' = 'info') {
  window.showToast?.(message, type);
}

function updateBadge(openCount: number) {
  const badge = document.getElementById('issues-nav-badge');
  if (!badge) return;
  badge.textContent = String(openCount || 0);
  badge.classList.toggle('hidden', !openCount);
}
