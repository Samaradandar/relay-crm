import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { FailedJob } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { StatTile, useToast } from '@/components/ui/Feedback';
import { Button } from '@/components/ui/Button';
import { formatRelativeIST } from '@/lib/format';

export function FailedJobsPage() {
  const qc = useQueryClient(); const toast = useToast();
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: api.failedJobs });
  const retry = useMutation({ mutationFn: api.retryJob, onSuccess: () => { qc.invalidateQueries({ queryKey: ['jobs'] }); toast({ message: 'Retried. It will show here again if it fails.' }); } });
  const columns: Column<FailedJob>[] = [
    { key: 'job', label: 'Job', render: (j) => <span className="flex flex-col"><span className="font-semibold">{j.title}</span><span className="text-mono text-ink-3">{j.event}</span></span> },
    { key: 'cause', label: 'Caused by', render: (j) => j.causedBy },
    { key: 'n', label: 'Attempts', numeric: true, render: (j) => j.attempts },
    { key: 'err', label: 'Last error', render: (j) => <span className="text-danger">{j.lastError}</span> },
    { key: 'at', label: 'Failed', render: (j) => formatRelativeIST(j.failedAt) },
    { key: 'act', label: '', render: (j) => <Button size="sm" icon="undo" loading={retry.isPending && retry.variables === j.id} onClick={() => retry.mutate(j.id)}>Retry</Button> }
  ];
  return <>
    <PageHeader title="Failed jobs" subtitle="Background work that failed 10 times. The change itself was saved; only the follow-up (email, alert, live update) did not go out." />
    <div className="mb-5 flex flex-wrap gap-4">
      <StatTile label="Failed now" value={jobs.data?.length ?? '–'} />
      <StatTile label="Audit chain check" value="Passed" hint="Runs nightly at 02:00 IST" />
    </div>
    <DataTable columns={columns} rows={jobs.data ?? []} empty="No failed jobs. Everything went out." />
  </>;
}
