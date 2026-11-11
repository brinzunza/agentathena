import { researchLabel, PHASE_LABEL, ENDED_LABEL } from './researchStatus';

describe('researchLabel', () => {
  test('before the first answer it says loading, and after a 404 that it is gone', () => {
    expect(researchLabel({})).toBe('loading…');
    expect(researchLabel({ lost: true, job: { status: 'running', phase: 'planning' } })).toBe('no longer available');
  });

  test('while running it names the phase', () => {
    expect(researchLabel({ job: { status: 'running', phase: 'writing' } })).toBe('writing the report');
    Object.keys(PHASE_LABEL).forEach((phase) => {
      expect(researchLabel({ job: { status: 'running', phase } })).toBe(PHASE_LABEL[phase]);
    });
  });

  test('once it has ended it says how', () => {
    Object.keys(ENDED_LABEL).forEach((status) => {
      expect(researchLabel({ job: { status, phase: 'researching' } })).toBe(ENDED_LABEL[status]);
    });
  });

  test('a state it has never heard of is shown as itself, never as undefined', () => {
    expect(researchLabel({ job: { status: 'paused' } })).toBe('paused');
    expect(researchLabel({ job: { status: 'running', phase: 'reviewing' } })).toBe('reviewing');
    expect(researchLabel({ job: { status: 'running' } })).toBe('working');
  });
});
