/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Customer } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Column, DataTable } from '@/components/ui/DataTable';

/** PRD Feature 9: customer status is Active / Churned only. */
export function CustomersPage() {
  const query = useQuery({ queryKey: ['customers'], queryFn: api.customers });
  const rows = query.data ?? [];

  const columns: Column<Customer>[] = [
    { key: 'account', label: 'Account', render: (c) => <Link to={`/app/accounts/${c.accountId}`} className="font-semibold text-brand-ink no-underline hover:underline">{c.accountName}</Link> },
    { key: 'owner', label: 'Account owner', render: (c) => c.accountOwnerName ?? '—' },
    { key: 'status', label: 'Status', render: (c) => c.status === 'churned' ? <Badge tone="danger" icon="x">Churned</Badge> : <Badge tone="success" icon="check">Active</Badge> }
  ];

  return <div className="flex flex-col gap-5">
    <PageHeader title="Customers" subtitle="Won accounts with an active or past subscription." />
    <DataTable columns={columns} rows={rows} empty={query.isLoading ? 'Loading…' : 'No customers yet.'} />
  </div>;
}
