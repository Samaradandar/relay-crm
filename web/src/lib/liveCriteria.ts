/**
 * Live mode: how to collect the proof for each exit criterion of the backend's published pipeline,
 * keyed by the backend criterion key (api/app/modules/pipeline/seeds/new_business.py CRITERIA).
 * Field keys are the PATCH /deals/{id} names. The list itself (labels, order, readiness) always
 * comes from GET /deals/{id} `progress.criteria`; this file only says which form to show.
 */
import { PLANS, PRICING_MODELS, TERMS, type ActivityKind, type FieldDef, type FieldType } from './pipeline';

export type DocKind = 'proposal' | 'agreement' | 'invoice' | 'advance' | 'prerequisite' | 'technical';

export type LiveProof =
  /** The proof is data already on the deal: one click ticks it once the backend says it is ready. */
  | { kind: 'confirm' }
  | { kind: 'fields'; fields: FieldDef[] }
  /** `when`: the activity must be dated in the future or already have happened. `status`: for
   * meetings, which of booked / held count. */
  | { kind: 'activity'; activity: ActivityKind[]; twoWay?: boolean; when?: 'future' | 'past'; status?: ('booked' | 'held')[]; explain: string; fields?: FieldDef[] }
  | { kind: 'checklist' }
  | { kind: 'contact_tag' }
  /** Name, phone and email of the decision maker: an existing contact is updated, or a new one added. */
  | { kind: 'decision_maker' }
  | { kind: 'lines'; fields: FieldDef[] }
  | { kind: 'document'; docKind: DocKind; status: string[]; file: boolean; fields?: FieldDef[] };

const f = (key: string, label: string, type: FieldType = 'text', extra: Partial<FieldDef> = {}): FieldDef => ({ key, label, type, ...extra });

const TERMS_FIELDS = [f('final_price_paise', 'Final price per month (₹)', 'money'), f('scope', 'Scope'), f('agreed_by', 'Agreed by', 'contact')];

export const LIVE_PROOFS: Record<string, LiveProof> = {
  // New business: Lead
  basic_info: { kind: 'confirm' },
  company_identified: { kind: 'confirm' },
  contact_verified: { kind: 'activity', activity: ['call', 'email', 'meeting'], twoWay: true, when: 'past', explain: 'Log a two-way call, email or meeting first.' },
  fits_profile: { kind: 'checklist' },
  // Qualification
  need_documented: { kind: 'fields', fields: [f('pain_point', 'Pain point and how we solve it', 'longtext', { placeholder: 'e.g. Misses ~40% of order calls during lunch rush' })] },
  meeting_scheduled: { kind: 'activity', activity: ['meeting'], status: ['booked', 'held'], explain: 'Book the date the customer gave (with attendees), or log a meeting that already took place.' },
  // Meeting Scheduled
  meeting_completed: { kind: 'activity', activity: ['meeting'], status: ['held'], explain: 'Mark the booked meeting as held (who attended, what was discussed), or log a meeting that was held.' },
  opportunity_defined: { kind: 'fields', fields: [f('use_case', 'Use case', 'longtext', { placeholder: 'e.g. Voice agent takes takeaway orders and books tables' })] },
  // Prospecting
  budget_confirmed: { kind: 'fields', fields: [f('budget_amount_paise', 'Budget per month (₹)', 'money'), f('budget_source', 'Source', 'text', { placeholder: 'e.g. Owner said on 6 Oct call' })] },
  decision_maker: { kind: 'decision_maker' },
  timeline_agreed: { kind: 'fields', fields: [f('expected_close_date', 'Expected close date', 'date'), f('expected_close_reason', 'Why this date')] },
  pricing_selected: { kind: 'lines', fields: [f('pricing_model', 'Pricing model', 'select', { options: PRICING_MODELS }), f('plan', 'Plan', 'select', { options: PLANS }), f('expected_minutes_per_month', 'Expected minutes per month', 'number')] },
  // Proposal
  // Proposal: the meeting, the customer's answer and the email are done in the Proposal panel above,
  // which ticks these as each step completes; a row that is ready can also be ticked here.
  walkthrough_logged: { kind: 'confirm' },
  feedback_recorded: { kind: 'confirm' },
  proposal_emailed: { kind: 'confirm' },
  decision_date_set: { kind: 'fields', fields: [f('decision_date', 'Decision date', 'date')] },
  // Revision / Revision 2
  terms_accepted: { kind: 'fields', fields: TERMS_FIELDS },
  agreement_sent: { kind: 'document', docKind: 'agreement', status: ['sent'], file: true },
  // Closure
  agreement_countersigned: { kind: 'document', docKind: 'agreement', status: ['countersigned'], file: true },
  advance_received: { kind: 'document', docKind: 'advance', status: ['received'], file: true, fields: [f('advance_amount_paise', 'Advance amount (₹)', 'money'), f('advance_received_by', 'Received by (name)', 'text', { placeholder: 'e.g. Neha Kapoor, Accounts' }), f('advance_received_contact', 'Their phone or email', 'text', { placeholder: 'e.g. +91 98110 22345 or neha@acme.in' }), f('advance_received_on', 'Received on', 'date')] },
  prerequisite_document: { kind: 'document', docKind: 'prerequisite', status: ['received'], file: true, fields: [f('prereq_documents', 'What the pre-requisite document covers', 'longtext', { placeholder: 'e.g. Business requirements and call flows' }), f('prereq_received_on', 'Received on', 'date')] },
  technical_document: { kind: 'document', docKind: 'technical', status: ['received'], file: true, fields: [f('technical_documents', 'What the technical document covers', 'longtext', { placeholder: 'e.g. API access details, integration spec, network requirements' }), f('technical_received_on', 'Received on', 'date')] },
  onboarding_info: { kind: 'fields', fields: [f('billing_contact', 'Billing contact', 'contact'), f('subscription_start_date', 'Subscription start', 'date'), f('pricing_model', 'Pricing model', 'select', { options: PRICING_MODELS }), f('contract_term', 'Contract term', 'select', { options: TERMS }), f('billing_frequency', 'Billing frequency', 'select', { options: ['Monthly', 'Quarterly', 'Yearly'] }), f('renewal_date', 'Renewal / contract end', 'date')] },
  // Onboarding
  kickoff_done: { kind: 'activity', activity: ['kickoff'], when: 'past', explain: 'Log the kickoff.', fields: [f('go_live_date', 'Planned go-live', 'date')] },
  agent_live: { kind: 'fields', fields: [f('live_date', 'Live date', 'date'), f('live_note', 'Note')] },
  first_invoice: { kind: 'document', docKind: 'invoice', status: ['draft', 'sent'], file: false },
  // Existing customers: the request type, requirement, new plan and subscription change are
  // filled in by the request and billing screens, so those are one-click once the backend says so.
  request_type_recorded: { kind: 'confirm' },
  requirement_documented: { kind: 'confirm' },
  contact_confirmed: { kind: 'activity', activity: ['call', 'email', 'meeting'], twoWay: true, when: 'past', explain: 'Log a two-way call, email or meeting first.' },
  proposal_sent: { kind: 'document', docKind: 'proposal', status: ['sent'], file: true },
  amendment_sent: { kind: 'document', docKind: 'agreement', status: ['sent'], file: true },
  amendment_countersigned: { kind: 'document', docKind: 'agreement', status: ['countersigned'], file: true },
  new_plan_recorded: { kind: 'confirm' },
  subscription_updated: { kind: 'confirm' },
  first_revised_invoice: { kind: 'document', docKind: 'invoice', status: ['draft', 'sent'], file: false },
};

/** Proof form for a backend criterion; unknown keys (a newly published pipeline) fall back by type. */
export function liveProof(key: string, proofType: string): LiveProof {
  const known = LIVE_PROOFS[key];
  if (known) return known;
  if (proofType === 'activity') return { kind: 'activity', activity: ['call', 'email', 'meeting', 'discovery', 'walkthrough', 'kickoff', 'note'], explain: 'Log the matching activity first.' };
  if (proofType === 'checklist') return { kind: 'checklist' };
  if (proofType === 'contact_tag') return { kind: 'contact_tag' };
  if (proofType === 'deal_lines') return { kind: 'lines', fields: [] };
  return { kind: 'confirm' };
}

/** Stage entry checks the backend enforces on a forward move (STAGES[].entry_checks), shown as a
 * to-do on the stage before so the rep is not surprised by a blocked move. */
export const ENTRY_DOCS: Record<string, { docKind: DocKind; status: string[]; label: string }> = {
  // none today: the proposal is made at the END of the Proposal stage, so nothing is needed to enter it
};

/** Money fields are typed in rupees and stored in paise. */
export const toApiValue = (fd: FieldDef, v: string): string | number => fd.type === 'money' ? Math.round(Number(v) * 100) : fd.type === 'number' ? Number(v) : v;
export const fromApiValue = (fd: FieldDef, v: string | undefined): string => v === undefined ? '' : fd.type === 'money' ? String(Number(v) / 100) : v;
