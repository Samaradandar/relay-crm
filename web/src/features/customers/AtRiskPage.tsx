/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { AtRiskCustomer, AtRiskReason } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Column, DataTable } from '@/components/ui/DataTable';

const REASON_LABEL: Record<AtRiskReason, string> = {
  cancellation_requested: 'Cancellation requested', payment_overdue: 'Payment overdue',
  minutes_ran_out: 'Minutes ran out', contract_ending_soon: 'Contract ending soon'
};

type Row = AtRiskCustomer & { id: string };

/** PRD Feature 9a: Team Leader (team) and Admin/Super admin (all) churn-risk view. */
export function AtRiskPage() {
  const query = useQuery({ queryKey: ['atRiskCustomers'], queryFn: api.atRiskCustomers });
  const rows: Row[] = (query.data ?? []).map((r) => ({ ...r, id: r.customerId }));

  const columns: Column<Row>[] = [
    { key: 'account', label: 'Account', render: (c) => <Link to={`/app/accounts/${c.accountId}`} className="font-semibold text-brand-ink no-underline hover:underline">{c.accountName}</Link> },
    { key: 'owner', label: 'Account owner', render: (c) => c.accountOwnerName ?? '—' },
    { key: 'reasons', label: 'Why', render: (c) => <div className="flex flex-wrap gap-1.5">{c.reasons.map((r) => <Badge key={r} tone="warning" icon="alert">{REASON_LABEL[r]}</Badge>)}</div> }
  ];

  return <div className="flex flex-col gap-5">
    <PageHeader title="Customers at risk" subtitle="Cancellation requested, payment overdue, minutes ran out, or contract ending within 14 days." />
    <DataTable columns={columns} rows={rows} empty={query.isLoading ? 'Loading…' : 'No customers at risk right now.'} />
  </div>;
}
