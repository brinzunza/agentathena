// How a background research job is described. The server (app/research.py) reports a status, and
// while it is running a phase; a test keeps these lists in step with it.

export const PHASE_LABEL = { planning: 'planning', researching: 'researching', writing: 'writing the report' };

export const ENDED_LABEL = {
  done: 'finished',
  cancelled: 'cancelled',
  error: 'failed',
  interrupted: 'interrupted',
};

// The words beside a research card's title. An unknown status or phase shows as itself, not as nothing.
export function researchLabel({ lost = false, job = null } = {}) {
  if (lost) return 'no longer available';
  if (!job) return 'loading…';
  if (job.status === 'running') return PHASE_LABEL[job.phase] ?? job.phase ?? 'working';
  return ENDED_LABEL[job.status] ?? String(job.status);
}
